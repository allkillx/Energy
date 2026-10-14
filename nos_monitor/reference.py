"""AEMO reference tables from the MMS Data Model monthly archive.

The live PUBLIC_NETWORK file carries only NETWORK_OUTAGEDETAIL and NETWORK_OUTAGECONSTRAINTSET
(verified against PUBLIC_NETWORK_20261014123018, 2026-10-14). Region, substation/equipment
descriptions and status descriptions live in tables AEMO publishes monthly:

    https://nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM/<YYYY>/MMSDM_<YYYY>_<MM>/
        MMSDM_Historical_Data_SQLLoader/DATA/PUBLIC_ARCHIVE#<TABLE>#FILE01#<YYYYMM>010000.zip

The newest month is found from the directory listings (nothing hard-coded). GENCONSET in each
monthly archive is incremental (only versions effective that month), so the last N months are
combined and the newest version of each constraint set wins; sets older than that stay unknown.
"""

from __future__ import annotations

import json
import os
import re

from . import mms_csv, nemweb
from .timeutil import iso, now_nem, parse_nem

ARCHIVE_ROOT = "https://nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM/"
LOOKUP_TABLES = ("NETWORK_SUBSTATIONDETAIL", "NETWORK_OUTAGESTATUSCODE", "NETWORK_EQUIPMENTDETAIL")
GENCONSET_MONTHS = 12
REFRESH_DAYS = 7
_MONTH_RE = re.compile(r"MMSDM_(\d{4})_(\d{2})/?$")


def _dirs(getter, url: str) -> list[nemweb.ListingEntry]:
    html = nemweb._get(getter, url, "reference-listing").decode("utf-8", errors="replace")
    out = []
    for m in re.finditer(r'<a\s+href="([^"]+/)"\s*>([^<]+)</a>', html, re.IGNORECASE):
        out.append(nemweb.ListingEntry(m.group(2).strip(), nemweb.urljoin(url, m.group(1)), None, None))
    return out


def find_months(getter=nemweb.http_get, limit: int = GENCONSET_MONTHS) -> list[tuple[str, str]]:
    """Newest-first [(YYYY_MM, DATA dir URL)] found by walking the archive listing."""
    years = sorted((e for e in _dirs(getter, ARCHIVE_ROOT) if re.fullmatch(r"\d{4}", e.name)),
                   key=lambda e: e.name, reverse=True)
    months = []
    for y in years:
        for e in _dirs(getter, y.url):
            m = _MONTH_RE.search(e.url)
            if m:
                months.append((f"{m.group(1)}_{m.group(2)}", e.url + "MMSDM_Historical_Data_SQLLoader/DATA/"))
        if len(months) >= limit:
            break
    months.sort(reverse=True)
    return months[:limit]


def _table_entry(getter, data_url: str, table: str) -> nemweb.ListingEntry | None:
    html = nemweb._get(getter, data_url, "reference-listing").decode("utf-8", errors="replace")
    for e in nemweb.parse_listing(html, data_url):
        name = e.name.replace("%23", "#")
        if f"#{table}#FILE" in name and name.lower().endswith(".zip"):
            return e
    return None


class Reference:
    """Cached reference tables under <var_dir>/reference/."""

    def __init__(self, var_dir: str, getter=nemweb.http_get):
        self.dir = os.path.join(var_dir, "reference")
        self.getter = getter
        os.makedirs(self.dir, exist_ok=True)
        self.meta_path = os.path.join(self.dir, "meta.json")
        self._mms: mms_csv.MmsFile | None = None

    def meta(self) -> dict:
        try:
            with open(self.meta_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def is_stale(self) -> bool:
        fetched = parse_nem(self.meta().get("fetched_at"))
        return fetched is None or (now_nem() - fetched).days >= REFRESH_DAYS

    def refresh(self) -> dict:
        """Download the lookup tables (newest month) and GENCONSET (last N months). Raises FetchError."""
        months = find_months(self.getter)
        if not months:
            raise nemweb.FetchError("reference-listing", ARCHIVE_ROOT, "no MMSDM month directories found")
        files: dict[str, list[str]] = {}
        newest, newest_url = months[0]
        for table in LOOKUP_TABLES:
            entry = _table_entry(self.getter, newest_url, table)
            if entry is None:
                raise nemweb.FetchError("reference-listing", newest_url, f"{table} not in archive month {newest}")
            files[table] = [self._save(entry)]
        files["GENCONSET"] = []
        for month, url in months:
            entry = _table_entry(self.getter, url, "GENCONSET")
            if entry is not None:
                files["GENCONSET"].append(self._save(entry))
        meta = {"fetched_at": iso(now_nem()), "newest_month": newest, "genconset_months": [m for m, _ in months],
                "files": files}
        with open(self.meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=1)
        self._mms = None
        return meta

    def _save(self, entry: nemweb.ListingEntry) -> str:
        name = entry.name.replace("%23", "#").replace("#", "_")
        path = os.path.join(self.dir, name)
        if not os.path.exists(path):
            data = nemweb.download(entry, self.getter)
            with open(path, "wb") as fh:
                fh.write(data)
        return name

    def lookup_tables(self) -> mms_csv.MmsFile:
        """Substation, status-code and equipment tables from the cached newest month (empty if none cached)."""
        if self._mms is None:
            merged = mms_csv.MmsFile()
            for table in LOOKUP_TABLES:
                for name in self.meta().get("files", {}).get(table, []):
                    part = mms_csv.parse_file(os.path.join(self.dir, name))
                    for tname, t in part.tables.items():
                        if tname == table:
                            merged.tables[tname] = t
            self._mms = merged
        return self._mms

    def genconset_pairs(self) -> list[tuple[str, str]]:
        """(GENCONSETID, GENCONID) for the newest (EFFECTIVEDATE, VERSIONNO) of each set across cached months."""
        best: dict[str, tuple] = {}
        members: dict[tuple, set] = {}
        for name in self.meta().get("files", {}).get("GENCONSET", []):
            for t in mms_csv.parse_file(os.path.join(self.dir, name)).tables.values():
                if "GENCONSETID" not in t.columns or "GENCONID" not in t.columns:
                    continue
                for r in t.rows:
                    key = (r.get("EFFECTIVEDATE", ""), int(r.get("VERSIONNO") or 0))
                    sid = r["GENCONSETID"]
                    members.setdefault((sid, key), set()).add(r["GENCONID"])
                    if sid not in best or key > best[sid]:
                        best[sid] = key
        return sorted((sid, gid) for sid, key in best.items() for gid in members[(sid, key)])

    def label(self) -> str | None:
        m = self.meta()
        if not m:
            return None
        months = m.get("genconset_months", [])
        return (f"AEMO MMSDM monthly archive (lookups {m.get('newest_month')}; GENCONSET "
                f"{months[-1] if months else '?'}..{months[0] if months else '?'})")


def merge_lookups(mms: mms_csv.MmsFile, ref: mms_csv.MmsFile) -> list[str]:
    """Add reference tables the file itself lacks. Returns the table names that came from the reference."""
    added = []
    for name, table in ref.tables.items():
        if name not in mms.tables:
            mms.tables[name] = table
            added.append(name)
    return added
