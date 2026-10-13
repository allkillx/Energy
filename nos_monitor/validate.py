"""Field-level validation of a PUBLIC_NETWORK file.

Answers "can outage, status and constraint-set fields actually be read from
this file?" with counts, so a real AEMO file can be checked before it is
trusted. Errors make the file unusable; warnings are reported but tolerated.
"""

from __future__ import annotations

from datetime import datetime

from .mms_csv import MmsFile
from .outages import T_CONSET, T_EQUIPMENT, T_OUTAGE, T_STATUS, T_SUBSTATION, normalise
from .timeutil import now_nem, parse_nem

REQUIRED = {
    T_OUTAGE: ["OUTAGEID", "SUBSTATIONID", "EQUIPMENTTYPE", "EQUIPMENTID", "STARTTIME", "ENDTIME", "OUTAGESTATUSCODE"],
    T_STATUS: ["OUTAGESTATUSCODE", "DESCRIPTION"],
    T_CONSET: ["OUTAGEID", "GENCONSETID"],
    T_SUBSTATION: ["SUBSTATIONID", "REGIONID"],
    T_EQUIPMENT: ["SUBSTATIONID", "EQUIPMENTTYPE", "EQUIPMENTID"],
}
OPTIONAL = {
    T_OUTAGE: ["ACTUAL_STARTTIME", "ACTUAL_ENDTIME", "REASON", "ISSECONDARY", "SUBMITTEDDATE", "LASTCHANGED",
               "RECALLTIMEDAY", "RECALLTIMENIGHT", "RESUBMITREASON", "RESUBMITOUTAGEID", "COMPANYREFCODE"],
    T_CONSET: ["STARTINTERVAL", "ENDINTERVAL"],
    T_SUBSTATION: ["DESCRIPTION", "VALIDFROM", "VALIDTO"],
    T_EQUIPMENT: ["DESCRIPTION", "VOLTAGE", "VALIDFROM", "VALIDTO"],
}
MAX_BAD_TIME_SHARE = 0.05


def _share(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def validate(mms: MmsFile, now: datetime | None = None) -> dict:
    now = now or now_nem()
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict = {"tables": {}}

    for name in REQUIRED:
        t = mms.table(name)
        if t is None:
            (errors if name == T_OUTAGE else warnings).append(f"table {name} is missing")
            continue
        stats["tables"][name] = {"version": t.version, "rows": len(t.rows)}
        missing = [c for c in REQUIRED[name] if c not in t.columns]
        if missing:
            (errors if name in (T_OUTAGE, T_STATUS, T_CONSET) else warnings).append(
                f"{name} lacks required columns: {', '.join(missing)}")
        opt_missing = [c for c in OPTIONAL.get(name, []) if c not in t.columns]
        if opt_missing:
            warnings.append(f"{name} lacks optional columns (shown as '—'): {', '.join(opt_missing)}")

    outages = mms.rows(T_OUTAGE)
    if mms.table(T_OUTAGE) is not None and not outages:
        errors.append(f"{T_OUTAGE} is empty")

    # outage fields
    if outages:
        n = len(outages)
        no_id = sum(1 for r in outages if not r.get("OUTAGEID"))
        bad_start = sum(1 for r in outages if parse_nem(r.get("STARTTIME")) is None)
        bad_end = sum(1 for r in outages if r.get("ENDTIME") and parse_nem(r.get("ENDTIME")) is None)
        no_status = sum(1 for r in outages if not r.get("OUTAGESTATUSCODE"))
        actual = sum(1 for r in outages if r.get("ACTUAL_STARTTIME"))
        stats["outage_rows"] = n
        stats["outage_ids"] = len({r.get("OUTAGEID") for r in outages if r.get("OUTAGEID")})
        stats["rows_with_actual_start"] = actual
        if no_id:
            errors.append(f"{no_id}/{n} outage rows have no OUTAGEID")
        for label, bad in (("STARTTIME", bad_start), ("ENDTIME", bad_end)):
            if _share(bad, n) > MAX_BAD_TIME_SHARE:
                errors.append(f"{bad}/{n} outage rows have an unparseable {label}")
            elif bad:
                warnings.append(f"{bad}/{n} outage rows have an unparseable {label}")
        if no_status:
            warnings.append(f"{no_status}/{n} outage rows have no OUTAGESTATUSCODE")

        # status codes resolve to descriptions?
        described = {r.get("OUTAGESTATUSCODE") for r in mms.rows(T_STATUS) if r.get("DESCRIPTION")}
        used = {r.get("OUTAGESTATUSCODE") for r in outages if r.get("OUTAGESTATUSCODE")}
        stats["status_codes_used"] = sorted(used)
        stats["status_codes_without_description"] = sorted(used - described)
        if used - described:
            warnings.append(f"status codes without a description: {', '.join(sorted(used - described))}")

        # substations resolve to regions?
        regions = {r.get("SUBSTATIONID"): r.get("REGIONID") for r in mms.rows(T_SUBSTATION) if r.get("REGIONID")}
        subs = {r.get("SUBSTATIONID") for r in outages if r.get("SUBSTATIONID")}
        stats["substations_used"] = len(subs)
        stats["substations_without_region"] = sorted(subs - regions.keys())
        if subs - regions.keys():
            warnings.append(f"{len(subs - regions.keys())}/{len(subs)} outage substations have no REGIONID")

    # constraint sets
    consets = mms.rows(T_CONSET)
    if consets:
        ids = {r.get("OUTAGEID") for r in outages}
        orphan = sum(1 for r in consets if r.get("OUTAGEID") not in ids)
        no_set = sum(1 for r in consets if not r.get("GENCONSETID"))
        stats["constraint_set_rows"] = len(consets)
        stats["constraint_sets_distinct"] = len({r.get("GENCONSETID") for r in consets if r.get("GENCONSETID")})
        stats["constraint_set_rows_without_outage"] = orphan
        if no_set:
            errors.append(f"{no_set}/{len(consets)} constraint set rows have no GENCONSETID")
        if orphan:
            warnings.append(f"{orphan}/{len(consets)} constraint set rows reference an OUTAGEID not in {T_OUTAGE}")

    # the normaliser must run end to end
    if not errors:
        try:
            records = normalise(mms, now)
            stats["records"] = len(records)
            stats["records_with_constraint_sets"] = sum(1 for r in records if r["constraint_sets"])
            stats["records_with_status_description"] = sum(1 for r in records if r["status_desc"])
            stats["regions"] = sorted({g for r in records for g in r["regions"]})
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"normalise failed: {type(exc).__name__}: {exc}")

    if mms.warnings:
        warnings.append(f"{len(mms.warnings)} CSV parse warnings (first: {mms.warnings[0]})")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


def format_report(report: dict) -> str:
    lines = ["VALID" if report["ok"] else "INVALID"]
    lines += [f"  error: {e}" for e in report["errors"]]
    lines += [f"  warning: {w}" for w in report["warnings"]]
    for k, v in report["stats"].items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)
