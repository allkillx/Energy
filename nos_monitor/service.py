"""Refresh orchestration and page state assembly.

Rules:
  * A failed fetch/parse never replaces the last good snapshot.
  * A file whose NETWORK_OUTAGEDETAIL is missing or empty is rejected (treated as
    a bad download), so an empty table can't wipe out the page.
  * The same upstream file is never stored twice; polling just records a check.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import timedelta

from . import mms_csv, nemweb, neopoint, neopoint_nos
from .diff import diff_snapshots
from .locations import Locations
from .market import adjacent_interconnectors, observe_outage, summarise_dispatch
from .outages import annotate, normalise
from .store import Store
from .timeutil import iso, now_nem, parse_nem
from .validate import validate


class ValidationError(ValueError):
    def __init__(self, report: dict):
        super().__init__("file failed validation, keeping last good data: " + "; ".join(report["errors"]))
        self.report = report

UPSTREAM_STALE_MINUTES = 90  # NOS publishes roughly every 30 min; 3 missed cycles = stale
MARKET_STALE_MINUTES = 15
MIN_REFRESH_GAP_SECONDS = 60
KEEP_RAW_FILES = 20
KEEP_ENDED_DAYS = 2
NEOPOINT_STALE_MINUTES = 30  # no successful NEOpoint check for this long -> stale (NEOpoint gives no publish time)


class Monitor:
    def __init__(self, store: Store, locations: Locations, var_dir: str, *, getter=nemweb.http_get,
                 network_url=nemweb.NETWORK_URL, dispatch_url=nemweb.DISPATCHIS_URL,
                 enable_network=True, enable_market=True, source="nemweb"):
        self.store, self.locations, self.var_dir = store, locations, var_dir
        self.getter, self.network_url, self.dispatch_url = getter, network_url, dispatch_url
        self.enable_network, self.enable_market = enable_network, enable_market
        self.source = source  # "nemweb" (AEMO PUBLIC_NETWORK) or "neopoint" (NOS Planned Outages by Region)
        self._lock = threading.Lock()
        self._last_refresh = 0.0
        os.makedirs(os.path.join(var_dir, "raw"), exist_ok=True)

    # --- ingest ----------------------------------------------------------
    def ingest_network(self, mms: mms_csv.MmsFile, meta: dict, raw_rows: dict | None = None) -> dict:
        now = now_nem()
        report = validate(mms, now)
        if not report["ok"]:
            raise ValidationError(report)
        records = normalise(mms, now)
        if raw_rows is not None:  # show the source's own rows (e.g. NEOpoint) rather than the converted ones
            for rec in records:
                rec["raw"] = raw_rows.get(rec["outage_id"], rec["raw"])
        prev = self.store.latest_snapshot()
        changes = diff_snapshots(prev["records"], records) if prev else []
        meta = dict(meta)
        meta.setdefault("fetched_at", iso(now))
        meta["report_time"] = iso(parse_nem(mms.report_date)) if mms.report_date else None
        meta["tables"] = {n: {"version": t.version, "rows": len(t.rows), "columns": t.columns}
                          for n, t in mms.tables.items()}
        meta["warnings"] = mms.warnings
        meta["validation"] = report
        sid = self.store.add_snapshot(meta, records, changes, prev["id"] if prev else None)
        return {"snapshot_id": sid, "records": len(records), "changes": len(changes)}

    def import_file(self, path: str, kind: str = "import") -> dict:
        mms = mms_csv.parse_file(path)
        name = os.path.basename(path)
        nt = nemweb.timestamp_from_name(name)
        res = self.ingest_network(mms, {"source_file": name, "source_url": None, "source_kind": kind,
                                        "name_time": iso(nt)})
        self.store.log(iso(now_nem()), "network", True, f"imported {name}: {res['records']} outages, {res['changes']} changes", name)
        return res

    def ingest_neopoint_rows(self, rows: list[dict], kind: str, urls: list[str] | None = None,
                             files: list[str] | None = None) -> dict:
        """Store NEOpoint NOS rows as a snapshot. Identical content is not stored twice."""
        digest = hashlib.sha256(json.dumps(sorted(json.dumps(r, sort_keys=True) for r in rows)).encode()).hexdigest()[:12]
        regions = sorted({r["REGIONID"] for r in rows if r["REGIONID"]})
        source_file = f"NEOpoint NOS Planned Outages by Region [{','.join(regions)}] #{digest}"
        if self.store.has_source(source_file):
            return {"new": False, "file": source_file}
        info = {
            "provider": "NEOpoint", "report": neopoint_nos.REPORT, "regions": regions,
            "latest_submitted": iso(parse_nem(neopoint_nos.latest_submitted(rows))),
            "rows": len(rows), "files": files or [], "not_provided": neopoint_nos.NOT_PROVIDED,
            "notes": ["actual_starttime/actual_endtime are per constraint-set row and are shown on the constraint "
                      "set, not as outage actual times (meaning unconfirmed).",
                      "No planned/unplanned field: short-notice outages appear, completeness is unknown.",
                      "Times assumed to be NEM time (AEMO convention); NEOpoint does not state a time zone."],
        }
        res = self.ingest_network(neopoint_nos.to_mms(rows), {
            "source_file": source_file, "source_url": " | ".join(urls) if urls else None, "source_kind": kind,
            "source_info": info,
        }, raw_rows=neopoint_nos.raw_by_outage(rows))
        return {"new": True, "file": source_file, **res}

    def import_neopoint_files(self, paths: list[str]) -> dict:
        rows = []
        for path in paths:
            with open(path, "rb") as fh:
                rows += neopoint_nos.read_rows(fh.read())
        names = [os.path.basename(p) for p in paths]
        res = self.ingest_neopoint_rows(rows, "import", files=names)
        msg = (f"imported NEOpoint {', '.join(names)}: {res['records']} outages, {res['changes']} changes"
               if res["new"] else f"NEOpoint import identical to a stored snapshot: {', '.join(names)}")
        self.store.log(iso(now_nem()), "network", True, msg, res["file"])
        return res

    def _refresh_neopoint(self) -> dict:
        """Fetch every configured region of the NOS report; ingest only if all of them succeed."""
        at = iso(now_nem())
        try:
            regions = neopoint.instances("nos_outages")
            if not regions:
                raise nemweb.FetchError("config", "<NEOPOINT_INSTANCES_NOS_OUTAGES>", "no regions configured")
            rows, urls = [], []
            for region in regions:
                data, url = neopoint.fetch("nos_outages", region)
                try:
                    rows += neopoint_nos.read_rows(data)
                except neopoint_nos.NeopointFormatError as exc:
                    self._save_raw(f"neopoint_nos_{region or 'all'}.csv", data, rejected=True)
                    raise nemweb.FetchError("parse", url, str(exc)) from None
                urls.append(url)
            res = self.ingest_neopoint_rows(rows, "neopoint", urls=urls)
            msg = (f"fetched NEOpoint NOS ({', '.join(r or 'all' for r in regions)}): {res['records']} outages, "
                   f"{res['changes']} changes" if res["new"] else "NEOpoint NOS unchanged since last snapshot")
            self.store.log(at, "network", True, msg, res["file"], url=urls[0] if urls else None)
            return {"ok": True, **res}
        except nemweb.FetchError as exc:
            self.store.log(at, "network", False, neopoint.redact(str(exc)), url=exc.url, http_status=exc.status, stage=exc.stage)
            return {"ok": False, "error": neopoint.redact(str(exc)), **exc.as_dict()}
        except ValidationError as exc:
            self.store.log(at, "network", False, str(exc), stage="validate")
            return {"ok": False, "error": str(exc), "stage": "validate"}

    def import_dispatch_file(self, path: str, kind: str = "import") -> dict:
        mms = mms_csv.parse_file(path)
        name = os.path.basename(path)
        summary = summarise_dispatch(mms)
        summary["source_kind"] = kind
        self.store.add_market(name, iso(nemweb.timestamp_from_name(name)), iso(now_nem()), summary)
        self.store.log(iso(now_nem()), "dispatch", True, f"imported {name}", name)
        return summary

    # --- fetch -----------------------------------------------------------
    def refresh(self, force: bool = False) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"skipped": "another refresh is already running"}
        try:
            if not force and time.monotonic() - self._last_refresh < MIN_REFRESH_GAP_SECONDS:
                return {"skipped": f"last check was less than {MIN_REFRESH_GAP_SECONDS}s ago"}
            self._last_refresh = time.monotonic()
            out = {}
            if self.enable_network and self.source == "neopoint":
                out["network"] = self._refresh_neopoint()
            elif self.enable_network:
                out["network"] = self._refresh_feed("network", self.network_url, "PUBLIC_NETWORK_", self._ingest_network_bytes)
            if self.enable_market and self.source == "nemweb":
                out["dispatch"] = self._refresh_feed("dispatch", self.dispatch_url, "PUBLIC_DISPATCHIS_", self._ingest_dispatch_bytes)
            return out
        finally:
            self._lock.release()

    def _refresh_feed(self, feed, url, prefix, ingest) -> dict:
        at = iso(now_nem())
        try:
            entry = nemweb.find_latest(url, prefix, self.getter)
            already = self.store.has_source(entry.name) if feed == "network" else self.store.has_market_source(entry.name)
            if already:
                self.store.log(at, feed, True, f"no new file; latest is still {entry.name}", entry.name, url=entry.url)
                return {"ok": True, "new": False, "file": entry.name}
            data = nemweb.download(entry, self.getter)
            try:
                res = ingest(entry, data)
            except ValidationError as exc:
                self._save_raw(entry.name, data, rejected=True)
                raise nemweb.FetchError("validate", entry.url, str(exc)) from exc
            except Exception as exc:
                self._save_raw(entry.name, data, rejected=True)
                raise nemweb.FetchError("parse", entry.url, f"{type(exc).__name__}: {exc}") from exc
            self._save_raw(entry.name, data)
            self.store.log(at, feed, True, f"fetched {entry.name}", entry.name, url=entry.url)
            return {"ok": True, "new": True, "file": entry.name, **res}
        except nemweb.FetchError as exc:  # keep last good data, surface the error with URL/status/stage
            self.store.log(at, feed, False, str(exc), url=exc.url, http_status=exc.status, stage=exc.stage)
            return {"ok": False, "error": str(exc), **exc.as_dict()}
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self.store.log(at, feed, False, msg, url=url)
            return {"ok": False, "error": msg}

    def _ingest_network_bytes(self, entry: nemweb.ListingEntry, data: bytes) -> dict:
        mms = mms_csv.parse_zip_bytes(data)
        return self.ingest_network(mms, {
            "source_file": entry.name, "source_url": entry.url, "source_kind": "nemweb",
            "name_time": iso(entry.name_time), "listing_time": iso(entry.listing_time),
        })

    def _ingest_dispatch_bytes(self, entry: nemweb.ListingEntry, data: bytes) -> dict:
        summary = summarise_dispatch(mms_csv.parse_zip_bytes(data))
        summary["source_kind"] = "nemweb"
        self.store.add_market(entry.name, iso(entry.name_time), iso(now_nem()), summary)
        return {"binding": len(summary["binding_constraints"])}

    def _save_raw(self, name: str, data: bytes, rejected: bool = False):
        """Keep recent raw zips; rejected files are kept too (prefixed) so they can be inspected."""
        raw = os.path.join(self.var_dir, "raw")
        with open(os.path.join(raw, ("REJECTED_" if rejected else "") + name), "wb") as fh:
            fh.write(data)
        files = sorted((os.path.join(raw, f) for f in os.listdir(raw)), key=os.path.getmtime)
        for f in files[:-KEEP_RAW_FILES]:
            os.remove(f)

    # --- state for the page ----------------------------------------------
    def _feed_status(self) -> list[dict]:
        """Per-feed status for the page. Only the NOS feed has a confirmed field mapping so far."""
        out = []
        for feed, info in neopoint.configured_feeds().items():
            implemented = feed == "nos_outages"
            out.append({
                "feed": feed, "report": info["report"], "provider": "NEOpoint", "configured": info["configured"],
                "active": implemented and self.source == "neopoint" and self.enable_network,
                "status": ("mapped (columns verified against a real NEOpoint export)" if implemented
                           else "not available: no real NEOpoint sample yet, so no field mapping"),
            })
        return out

    def state(self) -> dict:
        now = now_nem()
        snap = self.store.latest_snapshot()
        market = self.store.latest_market()
        members = self.store.genconset_members()
        net_last = self.store.last_log("network")
        net_ok = self.store.last_log("network", ok=True)
        disp_last = self.store.last_log("dispatch")

        records = []
        if snap:
            changed = self.store.changed_ids_in_snapshot(snap["id"])
            records = snap.pop("records")
            annotate(records, now, changed, self.locations.interconnectors)
            cutoff = now - timedelta(days=KEEP_ENDED_DAYS)
            kept = []
            for rec in records:
                if rec["phase"] == "ended" and (parse_nem(rec.get("actual_end") or rec.get("end")) or now) < cutoff:
                    continue
                self.locations.annotate(rec)
                rec["adjacent_interconnectors"] = adjacent_interconnectors(rec["regions"])
                rec["market"] = observe_outage(rec, market, members)
                kept.append(rec)
            records = kept

        def age_min(t):
            dt = parse_nem(t)
            return round((now - dt).total_seconds() / 60, 1) if dt else None

        upstream_time = snap and (snap.get("name_time") or snap.get("listing_time") or snap.get("report_time"))
        upstream_age = age_min(upstream_time) if snap else None
        last_failed = bool(net_last and not net_last["ok"])
        info = (snap or {}).get("source_info") or {}
        stale_reasons = []
        if not snap:
            stale_reasons.append("No successful data yet")
        else:
            if upstream_age is not None and upstream_age > UPSTREAM_STALE_MINUTES:
                stale_reasons.append(f"Upstream file is {upstream_age:.0f} min old (threshold {UPSTREAM_STALE_MINUTES} min)")
            if last_failed:
                stale_reasons.append("The latest fetch failed; showing the last successful data")
            if snap["source_kind"] == "neopoint":
                ok_age = age_min(net_ok["at"]) if net_ok else None
                if ok_age is not None and ok_age > NEOPOINT_STALE_MINUTES:
                    stale_reasons.append(f"No successful NEOpoint check for {ok_age:.0f} min (threshold {NEOPOINT_STALE_MINUTES} min)")
            elif snap["source_kind"] != "nemweb":
                stale_reasons.append("Data comes from a manual import or sample, not automatic fetching")

        market_meta = None
        if market:
            mt = market.get("name_time") or iso(parse_nem(market.get("settlement_date")))
            market_meta = {k: market.get(k) for k in ("source_file", "name_time", "fetched_at", "settlement_date", "source_kind")}
            market_meta["age_minutes"] = age_min(mt)
            market_meta["stale"] = (market_meta["age_minutes"] or 0) > MARKET_STALE_MINUTES
            market_meta["binding_constraints"] = market.get("binding_constraints", [])[:50]
            market_meta["prices"] = market.get("prices", {})
            market_meta["spreads"] = market.get("spreads", [])
            market_meta["interconnectors"] = market.get("interconnectors", {})

        return {
            "now": iso(now),
            "source": None if not snap else {
                "file": snap["source_file"], "url": snap["source_url"], "kind": snap["source_kind"],
                "upstream_time": upstream_time, "name_time": snap["name_time"],
                "listing_time": snap["listing_time"], "report_time": snap["report_time"],
                "fetched_at": snap["fetched_at"], "upstream_age_minutes": upstream_age,
                "local_age_minutes": age_min(snap["fetched_at"]), "tables": snap["tables"],
                "warning_count": len(snap["warnings"]), "warnings": snap["warnings"][:20],
                "snapshot_id": snap["id"], "record_count": snap["record_count"],
                "validation": snap.get("validation"),
                "info": info or None,
            },
            "feeds": self._feed_status(),
            "stale": bool(stale_reasons), "stale_reasons": stale_reasons,
            "last_check": net_last, "last_success_check": net_ok,
            "last_error": net_last["message"] if last_failed else None,
            "last_error_info": {k: net_last.get(k) for k in ("at", "url", "http_status", "stage")} if last_failed else None,
            "dispatch_last_check": disp_last,
            "market": market_meta,
            "genconset": {"sets": len(members), "source": self.store.genconset_source()},
            "outages": records,
            "changes": self.store.recent_changes(300),
            "snapshots": self.store.snapshot_list(30),
            "fetch_log": self.store.recent_log(30),
            "locations": {"substations": len(self.locations.substations), "lines": len(self.locations.lines),
                          "interconnector_substations": len(self.locations.interconnectors),
                          "problems": self.locations.problems[:50]},
            "config": {"upstream_stale_minutes": UPSTREAM_STALE_MINUTES, "network_url": self.network_url,
                       "dispatch_url": self.dispatch_url, "auto_fetch": self.enable_network},
        }
