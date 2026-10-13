"""Suggest substation coordinates from a downloaded Geoscience Australia dataset, for human review.

Nothing is written to data/substation_locations.csv until a person marks a
candidate `confirmed=yes` and runs `locations-apply`. Name similarity is only
used to *rank* candidates, never to accept them.

Expected input: GA "Electricity Transmission Substations" exported as GeoJSON
(Point features) or CSV with latitude/longitude columns. Field names are
auto-detected and can be overridden.
"""

from __future__ import annotations

import csv
import difflib
import json
import os
import re

from .locations import _read_csv

REGION_STATES = {"NSW1": {"NSW", "NEW SOUTH WALES", "ACT", "AUSTRALIAN CAPITAL TERRITORY"},
                 "QLD1": {"QLD", "QUEENSLAND"}, "VIC1": {"VIC", "VICTORIA"},
                 "SA1": {"SA", "SOUTH AUSTRALIA"}, "TAS1": {"TAS", "TASMANIA"}}
NAME_FIELDS = ("name", "NAME", "Name", "substation_name", "SUBSTATION")
STATE_FIELDS = ("state", "STATE", "State")
ID_FIELDS = ("objectid", "OBJECTID", "id", "ID", "featuretype_id", "ga_guid")
LAT_FIELDS = ("latitude", "LATITUDE", "lat", "LAT", "y", "Y")
LON_FIELDS = ("longitude", "LONGITUDE", "lon", "LON", "long", "x", "X")

_NOISE = re.compile(r"\b(SUBSTATION|SWITCHING|SWITCHYARD|TERMINAL|STATION|ZONE|TRANSMISSION|KV|SS|TS|ZS|"
                    r"POWER|ELECTRICITY|\d+)\b")


def _norm(s: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper())
    return re.sub(r"\s+", " ", _NOISE.sub(" ", s)).strip()


def _pick(props: dict, candidates, override=None):
    if override:
        return props.get(override)
    for c in candidates:
        if props.get(c) not in (None, ""):
            return props[c]
    return None


def load_ga(path: str, name_field=None, state_field=None, id_field=None) -> list[dict]:
    feats = []
    if path.lower().endswith((".geojson", ".json")):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for i, f in enumerate(data.get("features", [])):
            g = f.get("geometry") or {}
            if g.get("type") != "Point":
                continue
            p = f.get("properties") or {}
            lon, lat = g["coordinates"][:2]
            feats.append({"name": _pick(p, NAME_FIELDS, name_field), "state": _pick(p, STATE_FIELDS, state_field),
                          "ref": _pick(p, ID_FIELDS, id_field) or f.get("id") or f"feature {i}", "lat": lat, "lon": lon})
    else:
        for i, r in enumerate(_read_csv(path)):
            try:
                lat, lon = float(_pick(r, LAT_FIELDS)), float(_pick(r, LON_FIELDS))
            except (TypeError, ValueError):
                continue
            feats.append({"name": _pick(r, NAME_FIELDS, name_field), "state": _pick(r, STATE_FIELDS, state_field),
                          "ref": _pick(r, ID_FIELDS, id_field) or f"row {i + 2}", "lat": lat, "lon": lon})
    return [f for f in feats if f["name"]]


def suggest(aemo: dict[str, tuple[str, str]], ga: list[dict], top: int = 3, min_score: float = 0.6) -> list[dict]:
    """aemo: {substation_id: (description, region)} -> candidate rows (unconfirmed)."""
    rows = []
    for sid, (desc, region) in sorted(aemo.items()):
        states = REGION_STATES.get(region)
        pool = [f for f in ga if not states or not f["state"] or str(f["state"]).upper() in states]
        keys = [k for k in {_norm(desc), _norm(sid)} if k]
        scored = []
        for f in pool:
            n = _norm(f["name"])
            score = max((difflib.SequenceMatcher(None, k, n).ratio() for k in keys), default=0)
            if score >= min_score:
                scored.append((score, f))
        scored.sort(key=lambda x: -x[0])
        for score, f in scored[:top]:
            rows.append({"substation_id": sid, "aemo_description": desc, "region": region,
                         "candidate_name": f["name"], "candidate_state": f["state"] or "",
                         "latitude": f["lat"], "longitude": f["lon"], "score": round(score, 3),
                         "source_ref": f["ref"], "confirmed": ""})
        if not scored:
            rows.append({"substation_id": sid, "aemo_description": desc, "region": region, "candidate_name": "",
                         "candidate_state": "", "latitude": "", "longitude": "", "score": "",
                         "source_ref": "", "confirmed": ""})
    return rows


SUGGEST_FIELDS = ["substation_id", "aemo_description", "region", "candidate_name", "candidate_state",
                  "latitude", "longitude", "score", "source_ref", "confirmed"]


def write_suggestions(rows: list[dict], path: str, dataset: str):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(f"# Candidates from {dataset}. Ranked by name similarity only - NOT verified.\n"
                 "# Review each row; set confirmed=yes only for a correct match (one per substation_id),\n"
                 "# then run: python -m nos_monitor locations-apply <this file> --dataset \"<dataset name and version>\"\n")
        w = csv.DictWriter(fh, fieldnames=SUGGEST_FIELDS)
        w.writeheader()
        w.writerows(rows)


def apply_confirmed(review_path: str, target_path: str, dataset: str) -> tuple[int, list[str]]:
    existing = {r.get("substation_id") for r in _read_csv(target_path)}
    added, problems, seen = [], [], set()
    for r in _read_csv(review_path):
        if (r.get("confirmed") or "").strip().lower() != "yes":
            continue
        sid = r["substation_id"]
        if sid in seen:
            problems.append(f"{sid}: more than one confirmed candidate; skipped the extra one")
            continue
        seen.add(sid)
        if sid in existing:
            problems.append(f"{sid}: already in {os.path.basename(target_path)}; not overwritten")
            continue
        added.append([sid, r.get("candidate_name", ""), r["latitude"], r["longitude"], dataset,
                      r.get("source_ref", ""), f"matched to AEMO '{r.get('aemo_description', '')}'; human-confirmed"])
    if added:
        with open(target_path, "a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(added)
    return len(added), problems
