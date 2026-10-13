"""Map NEOpoint "106 Flows and Constraints\\NOS Planned Outages by Region" CSV into the NETWORK_* table shapes.

The column list below is taken verbatim from a real NEOpoint export
(tests/fixtures/neopoint/nos_planned_outages_NSW1_from_2026-10-12.csv, received 2026-10-13).
If NEOpoint renames or drops any of these columns the conversion fails loudly instead of guessing.

Findings from that export, which drive the mapping:
  * One row per outage x substation x equipment x constraint set (denormalised); exact duplicate rows occur.
  * No planned/unplanned classification column. Outages submitted after their start do appear, so
    short-notice entries are included, but completeness for unplanned outages cannot be confirmed.
  * actual_starttime / actual_endtime vary between constraint-set rows of the same outage and can lie
    far outside the proposed window, so they are NOT treated as outage actual times. They are kept on
    the constraint-set entry as "source actual start/end (meaning unconfirmed)".
  * Not provided at all: reason, secondary flag, last changed, resubmission fields, voltage,
    constraint-set start/end intervals.
"""

from __future__ import annotations

import csv
import io

from .mms_csv import MmsFile, MmsTable
from .outages import T_CONSET, T_EQUIPMENT, T_OUTAGE, T_STATUS, T_SUBSTATION

REPORT = "106 Flows and Constraints\\NOS Planned Outages by Region"
EXPECTED_COLUMNS = [
    "actual_starttime", "actual_endtime", "outageid", "REGIONID", "ownerid", "substationid",
    "substation_DESCRIPTION", "equipmenttype", "equipmentid", "equipment_DESCRIPTION", "genconsetid",
    "setDESCRIPTION", "proposes_start_time", "proposes_end_time", "submitteddate", "outagestatuscode",
    "outage_status_DESCRIPTION", "recall_minutes_day", "recall_minutes_night", "companyrefcode",
]
# Fields the page can show from AEMO's own tables but this report does not carry.
NOT_PROVIDED = {
    "reason": "Reason",
    "is_secondary": "Secondary flag",
    "last_changed": "Last changed",
    "resubmit_reason": "Resubmission reason / outage",
    "actual_start": "Outage actual start/end (report's actual_* columns are per constraint set; meaning unconfirmed)",
    "voltage": "Equipment voltage (only inside description text)",
    "constraint_set_interval": "Constraint set start/end interval",
    "outage_type": "Planned vs unplanned classification",
}


class NeopointFormatError(ValueError):
    pass


def read_rows(data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    if text.lstrip()[:1] == "<":
        raise NeopointFormatError("response is HTML, not CSV (login page or error page?)")
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    missing = [c for c in EXPECTED_COLUMNS if c not in header]
    if missing:
        raise NeopointFormatError(f"NEOpoint NOS CSV lacks expected columns: {', '.join(missing)} "
                                  f"(got: {', '.join(header)})")
    rows, seen = [], set()
    for r in reader:
        if not any((v or "").strip() for v in r.values()):
            continue
        key = tuple(r.get(c, "") for c in EXPECTED_COLUMNS)
        if key in seen:  # exact duplicates occur in real exports
            continue
        seen.add(key)
        rows.append({c: (r.get(c) or "").strip() for c in EXPECTED_COLUMNS})
    return rows


def _table(name, columns, rows):
    return MmsTable(name=name, version="neopoint", columns=columns, rows=rows)


def to_mms(rows: list[dict]) -> MmsFile:
    """Convert de-duplicated NEOpoint rows into NETWORK_* shaped tables so the AEMO pipeline can be reused."""
    outages, consets, statuses, subs, eqs = {}, {}, {}, {}, {}
    for r in rows:
        okey = (r["outageid"], r["substationid"], r["equipmenttype"], r["equipmentid"])
        outages.setdefault(okey, {
            "OUTAGEID": r["outageid"], "SUBSTATIONID": r["substationid"], "EQUIPMENTTYPE": r["equipmenttype"],
            "EQUIPMENTID": r["equipmentid"], "STARTTIME": r["proposes_start_time"], "ENDTIME": r["proposes_end_time"],
            "SUBMITTEDDATE": r["submitteddate"], "OUTAGESTATUSCODE": r["outagestatuscode"],
            "RECALLTIMEDAY": r["recall_minutes_day"], "RECALLTIMENIGHT": r["recall_minutes_night"],
            "COMPANYREFCODE": r["companyrefcode"],
        })
        ckey = (r["outageid"], r["genconsetid"])
        c = consets.setdefault(ckey, {"OUTAGEID": r["outageid"], "GENCONSETID": r["genconsetid"],
                                      "DESCRIPTION": r["setDESCRIPTION"], "ACTUAL_STARTTIME": r["actual_starttime"],
                                      "ACTUAL_ENDTIME": r["actual_endtime"]})
        if r["actual_starttime"] and (not c["ACTUAL_STARTTIME"] or r["actual_starttime"] < c["ACTUAL_STARTTIME"]):
            c["ACTUAL_STARTTIME"] = r["actual_starttime"]
        if r["actual_endtime"] > c["ACTUAL_ENDTIME"]:
            c["ACTUAL_ENDTIME"] = r["actual_endtime"]
        if r["outagestatuscode"]:
            statuses[r["outagestatuscode"]] = r["outage_status_DESCRIPTION"]
        subs.setdefault(r["substationid"], {"SUBSTATIONID": r["substationid"], "DESCRIPTION": r["substation_DESCRIPTION"],
                                            "REGIONID": r["REGIONID"], "OWNERID": r["ownerid"]})
        ekey = (r["substationid"], r["equipmenttype"], r["equipmentid"])
        e = eqs.setdefault(ekey, {"SUBSTATIONID": ekey[0], "EQUIPMENTTYPE": ekey[1], "EQUIPMENTID": ekey[2], "_d": []})
        if r["equipment_DESCRIPTION"] and r["equipment_DESCRIPTION"] not in e["_d"]:
            e["_d"].append(r["equipment_DESCRIPTION"])  # one equipment key can carry several descriptions
    for e in eqs.values():
        e["DESCRIPTION"] = " / ".join(e.pop("_d"))
    mms = MmsFile()
    mms.tables = {
        T_OUTAGE: _table(T_OUTAGE, ["OUTAGEID", "SUBSTATIONID", "EQUIPMENTTYPE", "EQUIPMENTID", "STARTTIME", "ENDTIME",
                                    "SUBMITTEDDATE", "OUTAGESTATUSCODE", "RECALLTIMEDAY", "RECALLTIMENIGHT",
                                    "COMPANYREFCODE"], list(outages.values())),
        T_CONSET: _table(T_CONSET, ["OUTAGEID", "GENCONSETID", "DESCRIPTION", "ACTUAL_STARTTIME", "ACTUAL_ENDTIME"],
                         list(consets.values())),
        T_STATUS: _table(T_STATUS, ["OUTAGESTATUSCODE", "DESCRIPTION"],
                         [{"OUTAGESTATUSCODE": k, "DESCRIPTION": v} for k, v in statuses.items()]),
        T_SUBSTATION: _table(T_SUBSTATION, ["SUBSTATIONID", "DESCRIPTION", "REGIONID", "OWNERID"], list(subs.values())),
        T_EQUIPMENT: _table(T_EQUIPMENT, ["SUBSTATIONID", "EQUIPMENTTYPE", "EQUIPMENTID", "DESCRIPTION"],
                            list(eqs.values())),
    }
    return mms


def raw_by_outage(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["outageid"], []).append(r)
    return out


def latest_submitted(rows: list[dict]) -> str | None:
    vals = [r["submitteddate"] for r in rows if r["submitteddate"]]
    return max(vals) if vals else None
