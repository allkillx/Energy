"""Command line entry point: python -m nos_monitor <command>."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

from . import mms_csv, nemweb, sample
from .locations import Locations
from .server import serve
from .service import Monitor
from .store import Store

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

    s = sub.add_parser("locations-template", help="list substations in the data that lack coordinates")
    s.add_argument("--out")
    s.set_defaults(func=cmd_locations_template)

    s = sub.add_parser("demo", help="serve synthetic sample data (no network needed)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
