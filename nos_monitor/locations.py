"""Explicitly maintained location mappings. Nothing here is guessed.

data/substation_locations.csv
    substation_id,name,latitude,longitude,source,source_ref,notes
    One row per AEMO SUBSTATIONID with a coordinate taken from a citable source
    (e.g. Geoscience Australia "Electricity Transmission Substations").
    Rows without a source or with blank/invalid coordinates are ignored.

data/line_locations.geojson
    FeatureCollection of LineString/MultiLineString features. Each feature's
    properties must contain `substation_id`, `equipment_id` (matching
    NETWORK_OUTAGEDETAIL) and `source`.

data/interconnector_substations.csv
    interconnector_id,substation_id,source,notes
    Substations that terminate an interconnector, used to flag outages that
    touch interconnector assets.
"""

from __future__ import annotations

import csv
import json
import os


def _read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def load_substations(path: str) -> tuple[dict[str, dict], list[str]]:
    out, problems = {}, []
    for i, r in enumerate(_read_csv(path), start=2):
        sid = (r.get("substation_id") or "").strip()
        if not sid:
            continue
        try:
            lat, lon = float(r.get("latitude", "")), float(r.get("longitude", ""))
        except ValueError:
            problems.append(f"{os.path.basename(path)} row {i}: {sid} has no valid coordinate")
            continue
        if not (-45 <= lat <= -9 and 112 <= lon <= 155):
            problems.append(f"{os.path.basename(path)} row {i}: {sid} coordinate outside Australia")
            continue
        if not (r.get("source") or "").strip():
            problems.append(f"{os.path.basename(path)} row {i}: {sid} has no source; ignored")
            continue
        out[sid] = {"substation_id": sid, "name": r.get("name", ""), "lat": lat, "lon": lon,
                    "source": r.get("source", ""), "source_ref": r.get("source_ref", ""), "notes": r.get("notes", "")}
    return out, problems


def load_lines(path: str) -> tuple[list[dict], list[str]]:
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    feats, problems = [], []
    for i, f in enumerate(data.get("features", [])):
        p = f.get("properties") or {}
        geom = f.get("geometry") or {}
        if geom.get("type") not in ("LineString", "MultiLineString"):
            problems.append(f"line feature {i}: unsupported geometry {geom.get('type')}")
            continue
        if not (p.get("substation_id") and p.get("equipment_id") and p.get("source")):
            problems.append(f"line feature {i}: needs substation_id, equipment_id and source")
            continue
        feats.append(f)
    return feats, problems


def load_interconnector_map(path: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in _read_csv(path):
        sid, ic = (r.get("substation_id") or "").strip(), (r.get("interconnector_id") or "").strip()
        if sid and ic:
            out.setdefault(sid, []).append(ic)
    return out


class Locations:
    def __init__(self, data_dir: str, extra_substation_files: list[str] | None = None,
                 extra_line_files: list[str] | None = None):
        self.data_dir = data_dir
        self.extra = extra_substation_files or []
        self.extra_lines = extra_line_files or []
        self.reload()

    def reload(self):
        self.substations, self.problems = load_substations(os.path.join(self.data_dir, "substation_locations.csv"))
        for path in self.extra:
            subs, probs = load_substations(path)
            self.substations.update(subs)
            self.problems += probs
        self.lines, probs = load_lines(os.path.join(self.data_dir, "line_locations.geojson"))
        self.problems += probs
        for path in self.extra_lines:
            lines, probs = load_lines(path)
            self.lines += lines
            self.problems += probs
        self.interconnectors = load_interconnector_map(os.path.join(self.data_dir, "interconnector_substations.csv"))
        self._line_index = {}
        for f in self.lines:
            p = f["properties"]
            self._line_index.setdefault((p["substation_id"], p["equipment_id"]), []).append(f)

    def annotate(self, rec: dict) -> None:
        """Attach map info. `mapped` is True only if at least one reliable coordinate/geometry exists."""
        points, lines = [], []
        for eq in rec["equipment"]:
            loc = self.substations.get(eq["substation_id"])
            eq["location"] = loc
            if loc and not any(p["substation_id"] == loc["substation_id"] for p in points):
                points.append(loc)
            for f in self._line_index.get((eq["substation_id"], eq["equipment_id"]), []):
                if f not in lines:
                    lines.append(f)
        rec["map_points"] = points
        rec["map_lines"] = lines
        rec["mapped"] = bool(points or lines)
