"""Command line entry point: python -m nos_monitor <command>."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

from . import ga_import, mms_csv, nemweb, neopoint, sample
from .validate import format_report, validate
from .locations import Locations
from .server import serve
from .service import Monitor
from .store import Store
from .timeutil import iso

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _monitor(args, **kw) -> Monitor:
    os.makedirs(args.var_dir, exist_ok=True)
    store = Store(os.path.join(args.var_dir, "nos.sqlite"))
    locations = Locations(args.data_dir, kw.pop("extra_subs", None), kw.pop("extra_lines", None))
    return Monitor(store, locations, args.var_dir, **kw)


def cmd_serve(args):
    m = _monitor(args, enable_network=not args.no_fetch, enable_market=not args.no_market and not args.no_fetch)
    serve(m, args.host, args.port, None if args.no_fetch else args.poll_seconds)


def cmd_refresh(args):
    m = _monitor(args, enable_market=not args.no_market)
    print(json.dumps(m.refresh(force=True), ensure_ascii=False, indent=2))


def cmd_import(args):
    m = _monitor(args)
    for path in args.files:
        res = m.import_dispatch_file(path) if args.dispatch else m.import_file(path)
        print(path, "->", json.dumps(res if not args.dispatch else {"binding": len(res["binding_constraints"])},
                                     ensure_ascii=False))


def cmd_import_genconset(args):
    """Accepts an MMS file containing a GENCONSET-like table (GENCONSETID, GENCONID) or a 2-column CSV."""
    pairs = []
    mms = mms_csv.parse_file(args.file)
    for t in mms.tables.values():
        if "GENCONSETID" in t.columns and "GENCONID" in t.columns:
            pairs += [(r["GENCONSETID"], r["GENCONID"]) for r in t.rows if r["GENCONSETID"] and r["GENCONID"]]
    if not pairs:
        with open(args.file, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                a = r.get("genconsetid") or r.get("GENCONSETID")
                b = r.get("genconid") or r.get("GENCONID")
                if a and b:
                    pairs.append((a, b))
    if not pairs:
        sys.exit("no GENCONSETID/GENCONID pairs found")
    m = _monitor(args)
    m.store.replace_genconset(pairs, os.path.basename(args.file))
    print(f"imported {len(pairs)} constraint-set members from {args.file}")


def cmd_inspect(args):
    print(mms_csv.describe(mms_csv.parse_file(args.file)))


def cmd_fetch_latest(args):
    url = nemweb.DISPATCHIS_URL if args.dispatch else nemweb.NETWORK_URL
    prefix = "PUBLIC_DISPATCHIS_" if args.dispatch else "PUBLIC_NETWORK_"
    entry = nemweb.find_latest(url, prefix)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, entry.name)
    with open(path, "wb") as fh:
        fh.write(nemweb.http_get(entry.url))
    print(f"{entry.name}  listing={entry.listing_time}  name_time={entry.name_time}\nsaved {path}")


def cmd_validate(args):
    report = validate(mms_csv.parse_file(args.file))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_report(report))
    sys.exit(0 if report["ok"] else 1)


def cmd_diagnose(args):
    """Step-by-step live check: listing -> download -> parse -> validate. Never falls back to sample data."""
    url = nemweb.DISPATCHIS_URL if args.dispatch else nemweb.NETWORK_URL
    prefix = "PUBLIC_DISPATCHIS_" if args.dispatch else "PUBLIC_NETWORK_"
    out = {"listing_url": url, "steps": []}

    def step(name, **info):
        out["steps"].append({"step": name, **info})
        print(f"[{name}] " + ", ".join(f"{k}={v}" for k, v in info.items()))

    try:
        entry = nemweb.find_latest(url, prefix)
        step("listing", ok=True, url=url, latest=entry.name, name_time=entry.name_time,
             listing_time=entry.listing_time, size=entry.size)
        data = nemweb.download(entry)
        step("download", ok=True, url=entry.url, bytes=len(data))
        try:
            mms = mms_csv.parse_zip_bytes(data)
        except Exception as exc:
            raise nemweb.FetchError("parse", entry.url, f"{type(exc).__name__}: {exc}") from exc
        step("parse", ok=True, tables=",".join(sorted(mms.tables)), report_time=mms.report_date)
        if args.save_fixture:
            os.makedirs(args.save_fixture, exist_ok=True)
            path = os.path.join(args.save_fixture, entry.name)
            with open(path, "wb") as fh:
                fh.write(data)
            step("save", ok=True, path=path)
        if not args.dispatch:
            report = validate(mms)
            out["validation"] = report
            print(format_report(report))
            if not report["ok"]:
                raise nemweb.FetchError("validate", entry.url, "; ".join(report["errors"]))
            if args.import_:
                m = _monitor(args)
                res = m.ingest_network(mms, {"source_file": entry.name, "source_url": entry.url, "source_kind": "nemweb",
                                             "name_time": iso(entry.name_time),
                                             "listing_time": iso(entry.listing_time)})
                step("import", ok=True, **res)
        out["ok"] = True
    except nemweb.FetchError as exc:
        step(exc.stage, ok=False, **{k: v for k, v in exc.as_dict().items() if k != "stage"})
        out["ok"] = False
        out["error"] = exc.as_dict()
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if out["ok"] else 1)


def cmd_locations_template(args):
    m = _monitor(args)
    snap = m.store.latest_snapshot()
    if not snap:
        sys.exit("no snapshot yet; run refresh or import first")
    seen = {}
    for rec in snap["records"]:
        for eq in rec["equipment"]:
            if eq["substation_id"] not in m.locations.substations:
                seen[eq["substation_id"]] = (eq["substation_name"], eq["region"])
    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    w = csv.writer(out)
    w.writerow(["substation_id", "name", "latitude", "longitude", "source", "source_ref", "notes"])
    for sid, (name, region) in sorted(seen.items()):
        w.writerow([sid, name, "", "", "", "", f"region={region}"])
    if args.out:
        out.close()
        print(f"wrote {len(seen)} unmapped substations to {args.out}")


def cmd_locations_suggest(args):
    m = _monitor(args)
    snap = m.store.latest_snapshot()
    if not snap:
        sys.exit("no snapshot yet; run refresh/diagnose --import or import first")
    aemo = {}
    for rec in snap["records"]:
        for eq in rec["equipment"]:
            if eq["substation_id"] not in m.locations.substations:
                aemo[eq["substation_id"]] = (eq["substation_name"], eq["region"])
    ga = ga_import.load_ga(args.ga_file, args.name_field, args.state_field, args.id_field)
    if not ga:
        sys.exit(f"no named point features read from {args.ga_file}; try --name-field")
    rows = ga_import.suggest(aemo, ga)
    ga_import.write_suggestions(rows, args.out, os.path.basename(args.ga_file))
    print(f"{len(aemo)} unmapped substations, {len(ga)} GA features -> {len(rows)} candidate rows in {args.out} (unconfirmed)")


def cmd_locations_apply(args):
    target = os.path.join(args.data_dir, "substation_locations.csv")
    n, problems = ga_import.apply_confirmed(args.review_file, target, args.dataset)
    for p in problems:
        print("skip:", p)
    print(f"added {n} confirmed coordinates to {target}")


def cmd_neopoint_inspect(args):
    """Show real columns / row count / time range of a NEOpoint CSV (a saved file, or a live configured feed)."""
    if args.file:
        with open(args.file, "rb") as fh:
            data, where = fh.read(), args.file
    else:
        try:
            data, where = neopoint.fetch(args.feed, args.instance)
        except nemweb.FetchError as exc:
            print(json.dumps({"feed": args.feed, "ok": False, **exc.as_dict()}, indent=2))
            sys.exit(1)
        if args.save:
            os.makedirs(args.save, exist_ok=True)
            path = os.path.join(args.save, f"neopoint_{args.feed}_{args.instance or 'all'}_{iso(neopoint.now_nem())[:16].replace(':', '')}.csv")
            with open(path, "wb") as fh:
                fh.write(data)
            print(f"saved raw response to {path} (contains no key)")
    print(json.dumps({"source": where, **neopoint.inspect_csv(data)}, indent=2, ensure_ascii=False))


def cmd_neopoint_status(args):
    feeds = neopoint.configured_feeds()
    print(json.dumps({"api_key_set": bool(os.environ.get(neopoint.KEY_ENV)), "feeds": feeds}, indent=2))


def cmd_demo(args):
    """Load two synthetic snapshots (to show diffs) into a separate var dir and serve without fetching."""
    args.var_dir = args.var_dir if args.var_dir != os.path.join(ROOT, "var") else os.path.join(ROOT, "var", "demo")
    db = os.path.join(args.var_dir, "nos.sqlite")
    if os.path.exists(db):
        os.remove(db)
    m = _monitor(args, enable_network=False, enable_market=False,
                 extra_subs=[os.path.join(ROOT, "samples", "demo_substation_locations.csv")],
                 extra_lines=[os.path.join(ROOT, "samples", "demo_line_locations.geojson")])
    gen = os.path.join(args.var_dir, "generated")
    os.makedirs(gen, exist_ok=True)
    files = [sample.network_csv(1), sample.network_csv(2)]
    for name, text in files:
        path = os.path.join(gen, name)
        with open(path, "w") as fh:
            fh.write(text)
        m.import_file(path, kind="sample")
    name, text = sample.dispatch_csv()
    path = os.path.join(gen, name)
    with open(path, "w") as fh:
        fh.write(text)
    m.import_dispatch_file(path, kind="sample")
    m.store.replace_genconset(sample.GENCONSET_SAMPLE, "DEMO sample GENCONSET")
    serve(m, args.host, args.port, None, demo=True)


def main(argv=None):
    p = argparse.ArgumentParser(prog="nos_monitor", description="NEM Network Outage Schedule monitor")
    p.add_argument("--var-dir", default=os.path.join(ROOT, "var"), help="SQLite + raw file directory")
    p.add_argument("--data-dir", default=os.path.join(ROOT, "data"), help="location mapping directory")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web page + background poller")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--poll-seconds", type=float, default=300, help="upstream check interval (default 300)")
    s.add_argument("--no-fetch", action="store_true", help="never contact NEMweb (use imported data)")
    s.add_argument("--no-market", action="store_true", help="skip DispatchIS market observations")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("refresh", help="check NEMweb once and store a new snapshot if available")
    s.add_argument("--no-market", action="store_true")
    s.set_defaults(func=cmd_refresh)

    s = sub.add_parser("import", help="import downloaded PUBLIC_NETWORK zip/CSV file(s)")
    s.add_argument("files", nargs="+")
    s.add_argument("--dispatch", action="store_true", help="files are PUBLIC_DISPATCHIS reports")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("import-genconset", help="import constraint-set -> constraint ID membership")
    s.add_argument("file")
    s.set_defaults(func=cmd_import_genconset)

    s = sub.add_parser("inspect", help="list tables/columns/row counts of an MMS zip or CSV")
    s.add_argument("file")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("fetch-latest", help="download the newest PUBLIC_NETWORK zip for inspection")
    s.add_argument("--out", default=os.path.join(ROOT, "var", "downloads"))
    s.add_argument("--dispatch", action="store_true")
    s.set_defaults(func=cmd_fetch_latest)

    s = sub.add_parser("validate", help="check that outage/status/constraint-set fields can be read from a file")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("diagnose", help="live check of NEMweb: listing, download, parse, validate (no fallback)")
    s.add_argument("--dispatch", action="store_true", help="check DispatchIS instead of Network")
    s.add_argument("--save-fixture", metavar="DIR", help="save the downloaded zip, e.g. tests/fixtures/real")
    s.add_argument("--import", dest="import_", action="store_true", help="also store it as a snapshot")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_diagnose)

    s = sub.add_parser("locations-template", help="list substations in the data that lack coordinates")
    s.add_argument("--out")
    s.set_defaults(func=cmd_locations_template)

    s = sub.add_parser("locations-suggest", help="rank Geoscience Australia substations as candidates for review")
    s.add_argument("ga_file", help="GA Electricity Transmission Substations as GeoJSON or CSV")
    s.add_argument("--out", default="location_candidates.csv")
    s.add_argument("--name-field")
    s.add_argument("--state-field")
    s.add_argument("--id-field")
    s.set_defaults(func=cmd_locations_suggest)

    s = sub.add_parser("locations-apply", help="append human-confirmed candidates to data/substation_locations.csv")
    s.add_argument("review_file")
    s.add_argument("--dataset", required=True, help='source label, e.g. "Geoscience Australia Electricity Transmission Substations (downloaded 2026-10-13)"')
    s.set_defaults(func=cmd_locations_apply)

    s = sub.add_parser("neopoint-status", help="show which NEOpoint feeds are configured (never prints the key)")
    s.set_defaults(func=cmd_neopoint_status)

    s = sub.add_parser("neopoint-inspect", help="report real columns, rows and time range of a NEOpoint CSV")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--feed", choices=sorted(neopoint.FEEDS), help="fetch this configured feed now (server-side)")
    g.add_argument("--file", help="inspect a saved CSV instead")
    s.add_argument("--instance", help="with --feed: value for {instances}, e.g. NSW1")
    s.add_argument("--save", metavar="DIR", help="with --feed: keep the raw response, e.g. tests/fixtures/neopoint")
    s.set_defaults(func=cmd_neopoint_inspect)

    s = sub.add_parser("demo", help="serve synthetic sample data (no network needed)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_demo)

    neopoint.load_dotenv()
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
