"""Compare two consecutive snapshots of normalised outage records."""

from __future__ import annotations

TIME_FIELDS = ("start", "end", "actual_start", "actual_end")

KIND_LABELS = {
    "added": "Added",
    "removed": "Removed",
    "time": "Time change",
    "status": "Status change",
    "constraint_sets": "Constraint set change",
    "equipment": "Equipment change",
}


def _conset_key(c: dict) -> tuple:
    return (c.get("id"), c.get("start_interval"), c.get("end_interval"),
            c.get("source_actual_start"), c.get("source_actual_end"))


def _equipment_key(e: dict) -> tuple:
    return (e.get("substation_id"), e.get("equipment_type"), e.get("equipment_id"))


def diff_snapshots(prev: list[dict], curr: list[dict]) -> list[dict]:
    """Return change events. Each event: {outage_id, kind, label, details:[{field, old, new}]}."""
    old = {r["outage_id"]: r for r in prev}
    new = {r["outage_id"]: r for r in curr}
    events: list[dict] = []

    def add(oid, kind, details=None, rec=None):
        r = rec or new.get(oid) or old.get(oid) or {}
        events.append(
            {
                "outage_id": oid,
                "kind": kind,
                "label": KIND_LABELS[kind],
                "details": details or [],
                "regions": r.get("regions", []),
                "substations": r.get("substations", []),
            }
        )

    for oid in sorted(new.keys() - old.keys()):
        add(oid, "added")
    for oid in sorted(old.keys() - new.keys()):
        add(oid, "removed", rec=old[oid])
    for oid in sorted(new.keys() & old.keys()):
        a, b = old[oid], new[oid]
        times = [{"field": f, "old": a.get(f), "new": b.get(f)} for f in TIME_FIELDS if a.get(f) != b.get(f)]
        if times:
            add(oid, "time", times)
        if a.get("status_code") != b.get("status_code"):
            add(oid, "status", [{"field": "status_code", "old": a.get("status_code"), "new": b.get("status_code")}])
        ca = sorted(_conset_key(c) for c in a.get("constraint_sets", []))
        cb = sorted(_conset_key(c) for c in b.get("constraint_sets", []))
        if ca != cb:
            ida = {c[0] for c in ca}
            idb = {c[0] for c in cb}
            details = [{"field": "constraint_set", "old": None, "new": i} for i in sorted(idb - ida)]
            details += [{"field": "constraint_set", "old": i, "new": None} for i in sorted(ida - idb)]
            if not details:
                details = [{"field": "constraint_set_interval", "old": str(ca), "new": str(cb)}]
            add(oid, "constraint_sets", details)
        ea = sorted(_equipment_key(e) for e in a.get("equipment", []))
        eb = sorted(_equipment_key(e) for e in b.get("equipment", []))
        if ea != eb:
            add(oid, "equipment", [{"field": "equipment", "old": "/".join(map(str, x)), "new": None} for x in ea if x not in eb]
                + [{"field": "equipment", "old": None, "new": "/".join(map(str, x))} for x in eb if x not in ea])
    return events
