"""Turn raw NETWORK_* tables into normalised outage records.

One record per OUTAGEID. NETWORK_OUTAGEDETAIL's key is
(OUTAGEID, SUBSTATIONID, EQUIPMENTTYPE, EQUIPMENTID, STARTTIME), so one outage
can cover several pieces of equipment; they are kept in `equipment`.
Column names are looked up defensively because they vary between MMS Data
Model versions.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .mms_csv import MmsFile
from .timeutil import iso, parse_nem

T_OUTAGE = "NETWORK_OUTAGEDETAIL"
T_CONSET = "NETWORK_OUTAGECONSTRAINTSET"
T_STATUS = "NETWORK_OUTAGESTATUSCODE"
T_SUBSTATION = "NETWORK_SUBSTATIONDETAIL"
T_EQUIPMENT = "NETWORK_EQUIPMENTDETAIL"

FOCUS_REGIONS = ("NSW1", "QLD1", "VIC1")
ALL_REGIONS = ("NSW1", "QLD1", "VIC1", "SA1", "TAS1")

# Common MMS equipment type codes. Unknown codes are shown raw.
EQUIPMENT_TYPE_LABELS = {
    "LINE": "Line",
    "TRANS": "Transformer",
    "CB": "Circuit breaker",
    "CAP": "Capacitor",
    "REAC": "Reactor",
    "SVC": "Static VAR compensator",
    "SYNC": "Synchronous condenser",
    "BUS": "Busbar",
    "DS": "Disconnector",
    "ISOL": "Isolator",
    "GEN": "Generator",
    "LOAD": "Load",
}

_CANCELLED_RE = re.compile(r"CANCEL|WITHDRAW|REJECT|DELET|RECALLED", re.IGNORECASE)


def _get(row: dict, *names: str) -> str:
    for n in names:
        v = row.get(n)
        if v:
            return v
    return ""


def _current_version(rows: list[dict], now: datetime) -> dict | None:
    """Pick the row valid at `now` from VALIDFROM/VALIDTO versioned rows."""
    best, best_from = None, None
    for r in rows:
        vf = parse_nem(r.get("VALIDFROM"))
        vt = parse_nem(r.get("VALIDTO"))
        valid_now = (vf is None or vf <= now) and (vt is None or now < vt)
        rank = (valid_now, vf or datetime.min.replace(tzinfo=now.tzinfo))
        if best is None or rank > best_from:
            best, best_from = r, rank
    return best


def build_lookups(mms: MmsFile, now: datetime):
    status = {
        r.get("OUTAGESTATUSCODE", ""): r.get("DESCRIPTION", "")
        for r in mms.rows(T_STATUS)
        if r.get("OUTAGESTATUSCODE")
    }
    by_sub: dict[str, list[dict]] = {}
    for r in mms.rows(T_SUBSTATION):
        by_sub.setdefault(r.get("SUBSTATIONID", ""), []).append(r)
    substations = {k: _current_version(v, now) for k, v in by_sub.items() if k}

    by_eq: dict[tuple, list[dict]] = {}
    for r in mms.rows(T_EQUIPMENT):
        key = (r.get("SUBSTATIONID", ""), r.get("EQUIPMENTTYPE", ""), r.get("EQUIPMENTID", ""))
        by_eq.setdefault(key, []).append(r)
    equipment = {k: _current_version(v, now) for k, v in by_eq.items()}

    consets: dict[str, list[dict]] = {}
    for r in mms.rows(T_CONSET):
        oid = r.get("OUTAGEID", "")
        if not oid:
            continue
        entry = {
            "id": _get(r, "GENCONSETID", "CONSTRAINTSETID"),
            "start_interval": iso(parse_nem(r.get("STARTINTERVAL"))),
            "end_interval": iso(parse_nem(r.get("ENDINTERVAL"))),
        }
        # Extra fields some sources carry (NEOpoint: set description, per-set actual_* of unconfirmed meaning).
        if r.get("DESCRIPTION"):
            entry["description"] = r["DESCRIPTION"]
        if r.get("ACTUAL_STARTTIME") or r.get("ACTUAL_ENDTIME"):
            entry["source_actual_start"] = iso(parse_nem(r.get("ACTUAL_STARTTIME")))
            entry["source_actual_end"] = iso(parse_nem(r.get("ACTUAL_ENDTIME")))
        consets.setdefault(oid, []).append(entry)
    return status, substations, equipment, consets


def normalise(mms: MmsFile, now: datetime) -> list[dict]:
    """Return outage records (JSON-serialisable; times as NEM ISO strings)."""
    status, substations, equipment, consets = build_lookups(mms, now)
    grouped: dict[str, list[dict]] = {}
    for r in mms.rows(T_OUTAGE):
        oid = r.get("OUTAGEID", "")
        if oid:
            grouped.setdefault(oid, []).append(r)

    records = []
    for oid, rows in grouped.items():
        items = []
        for r in rows:
            sub_id = r.get("SUBSTATIONID", "")
            eq_type = r.get("EQUIPMENTTYPE", "")
            eq_id = r.get("EQUIPMENTID", "")
            sub = substations.get(sub_id) or {}
            eq = equipment.get((sub_id, eq_type, eq_id)) or {}
            items.append(
                {
                    "substation_id": sub_id,
                    "substation_name": sub.get("DESCRIPTION", ""),
                    "region": sub.get("REGIONID", ""),
                    "owner": sub.get("OWNERID", ""),
                    "equipment_type": eq_type,
                    "equipment_type_label": EQUIPMENT_TYPE_LABELS.get(eq_type.upper(), eq_type),
                    "equipment_id": eq_id,
                    "equipment_desc": eq.get("DESCRIPTION", ""),
                    "voltage": eq.get("VOLTAGE", ""),
                    "element_id": _get(r, "ELEMENTID") or eq.get("ELEMENTID", ""),
                    "start": iso(parse_nem(r.get("STARTTIME"))),
                    "end": iso(parse_nem(r.get("ENDTIME"))),
                    "actual_start": iso(parse_nem(r.get("ACTUAL_STARTTIME"))),
                    "actual_end": iso(parse_nem(r.get("ACTUAL_ENDTIME"))),
                }
            )
        first = max(rows, key=lambda r: r.get("LASTCHANGED", ""))
        code = first.get("OUTAGESTATUSCODE", "")
        starts = [i["start"] for i in items if i["start"]]
        ends = [i["end"] for i in items if i["end"]]
        a_starts = [i["actual_start"] for i in items if i["actual_start"]]
        a_ends = [i["actual_end"] for i in items if i["actual_end"]]
        rec = {
            "outage_id": oid,
            "equipment": sorted(items, key=lambda i: (i["substation_id"], i["equipment_type"], i["equipment_id"])),
            "regions": sorted({i["region"] for i in items if i["region"]}),
            "substations": sorted({i["substation_id"] for i in items if i["substation_id"]}),
            "start": min(starts) if starts else None,
            "end": max(ends) if ends else None,
            # Only report actual end when every item has one, otherwise the outage is still running.
            "actual_start": min(a_starts) if a_starts else None,
            "actual_end": max(a_ends) if a_ends and len(a_ends) == len(items) else None,
            "status_code": code,
            "status_desc": status.get(code, ""),
            "reason": first.get("REASON", ""),
            "resubmit_reason": first.get("RESUBMITREASON", ""),
            "resubmit_outage_id": first.get("RESUBMITOUTAGEID", ""),
            "recall_time_day": first.get("RECALLTIMEDAY", ""),
            "recall_time_night": first.get("RECALLTIMENIGHT", ""),
            "is_secondary": first.get("ISSECONDARY", ""),
            "company_ref": first.get("COMPANYREFCODE", ""),
            "submitted": iso(parse_nem(first.get("SUBMITTEDDATE"))),
            "last_changed": iso(parse_nem(first.get("LASTCHANGED"))),
            "constraint_sets": sorted(consets.get(oid, []), key=lambda c: (c["id"], c["start_interval"] or "")),
            "raw": rows,
        }
        records.append(rec)
    records.sort(key=lambda r: (r["start"] or "", r["outage_id"]))
    return records


def phase(rec: dict, now: datetime) -> tuple[str, str]:
    """Classify an outage relative to `now`.

    Returns (phase, basis): phase in cancelled|ended|active|upcoming|unknown,
    basis 'actual' when actual start/end times drove the decision, else 'planned'.
    """
    if _CANCELLED_RE.search(rec.get("status_code", "") + " " + rec.get("status_desc", "")):
        return "cancelled", "status"
    a_start, a_end = parse_nem(rec.get("actual_start")), parse_nem(rec.get("actual_end"))
    start, end = parse_nem(rec.get("start")), parse_nem(rec.get("end"))
    if a_end and a_end <= now:
        return "ended", "actual"
    if a_start and a_start <= now:
        return "active", "actual"
    if start is None:
        return "unknown", "planned"
    if end and end <= now:
        return "ended", "planned"
    if start <= now:
        return "active", "planned"
    return "upcoming", "planned"


def annotate(records: list[dict], now: datetime, changed_ids: set[str], interconnector_map: dict) -> None:
    """Add phase, interconnector hints and 'attention' reasons in place.

    Attention rule (documented in README):
      * still relevant (active, or upcoming within 24 h), AND
        has an associated constraint set or touches a mapped interconnector substation; OR
      * changed in the most recent snapshot diff and not ended/cancelled.
    """
    soon = now + timedelta(hours=24)
    for rec in records:
        ph, basis = phase(rec, now)
        rec["phase"], rec["phase_basis"] = ph, basis
        ics = sorted({ic for s in rec["substations"] for ic in interconnector_map.get(s, [])})
        rec["interconnectors"] = ics
        start = parse_nem(rec["start"])
        relevant = ph == "active" or (ph == "upcoming" and start is not None and start <= soon)
        reasons = []
        if relevant and ph == "upcoming":
            reasons.append("Starts within 24 h")
        if relevant and rec["constraint_sets"]:
            reasons.append("Has constraint set")
        if relevant and ics:
            reasons.append("Touches mapped interconnector substation")
        if rec["outage_id"] in changed_ids and ph not in ("ended", "cancelled"):
            reasons.append("Changed in latest snapshot")
        attention = (relevant and bool(rec["constraint_sets"] or ics)) or "Changed in latest snapshot" in reasons
        rec["attention"] = attention
        rec["attention_reasons"] = reasons if attention else []
