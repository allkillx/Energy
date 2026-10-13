"""Market observations from public DispatchIS reports (5-minute dispatch).

These are *observations in the same time window*, never evidence that an
outage caused a constraint to bind or a price spread to open.
"""

from __future__ import annotations

from .mms_csv import MmsFile

# Interconnector -> (from region, to region), positive flow direction per AEMO convention.
INTERCONNECTOR_REGIONS = {
    "N-Q-MNSP1": ("NSW1", "QLD1"),
    "NSW1-QLD1": ("NSW1", "QLD1"),
    "VIC1-NSW1": ("VIC1", "NSW1"),
    "V-SA": ("VIC1", "SA1"),
    "V-S-MNSP1": ("VIC1", "SA1"),
    "T-V-MNSP1": ("TAS1", "VIC1"),
}

REGION_PAIRS = [("QLD1", "NSW1"), ("NSW1", "VIC1"), ("VIC1", "SA1"), ("TAS1", "VIC1")]


def adjacent_interconnectors(regions: list[str]) -> list[str]:
    rs = set(regions)
    return sorted(ic for ic, (a, b) in INTERCONNECTOR_REGIONS.items() if a in rs or b in rs)


def _f(v: str):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _base_rows(rows: list[dict]) -> list[dict]:
    """Prefer the non-intervention pricing run (INTERVENTION = 0) when both are present."""
    base = [r for r in rows if r.get("INTERVENTION", "0") in ("0", "")]
    return base or rows


def summarise_dispatch(mms: MmsFile) -> dict:
    constraints = _base_rows(mms.rows("DISPATCH_CONSTRAINT"))
    prices = _base_rows(mms.rows("DISPATCH_PRICE"))
    ics = _base_rows(mms.rows("DISPATCH_INTERCONNECTORRES"))
    settlement = next((r.get("SETTLEMENTDATE") for r in constraints + prices + ics if r.get("SETTLEMENTDATE")), None)

    binding = []
    for r in constraints:
        mv = _f(r.get("MARGINALVALUE"))
        if mv:
            binding.append({
                "constraint_id": r.get("CONSTRAINTID", ""),
                "marginal_value": mv,
                "rhs": _f(r.get("RHS")),
                "lhs": _f(r.get("LHS")),
                "violation_degree": _f(r.get("VIOLATIONDEGREE")),
            })
    binding.sort(key=lambda b: -abs(b["marginal_value"]))

    rrp = {r.get("REGIONID", ""): _f(r.get("RRP")) for r in prices if r.get("REGIONID")}
    spreads = [
        {"pair": f"{a}-{b}", "a": a, "b": b, "spread": round(rrp[a] - rrp[b], 2)}
        for a, b in REGION_PAIRS
        if rrp.get(a) is not None and rrp.get(b) is not None
    ]
    flows = {}
    for r in ics:
        icid = r.get("INTERCONNECTORID", "")
        if not icid:
            continue
        flows[icid] = {
            "mwflow": _f(r.get("MWFLOW")),
            "metered_mwflow": _f(r.get("METEREDMWFLOW")),
            "export_limit": _f(r.get("EXPORTLIMIT")),
            "import_limit": _f(r.get("IMPORTLIMIT")),
            "marginal_value": _f(r.get("MARGINALVALUE")),
            "regions": INTERCONNECTOR_REGIONS.get(icid),
        }
    return {
        "settlement_date": settlement,
        "constraint_rows": len(constraints),
        "binding_constraints": binding,
        "prices": rrp,
        "spreads": spreads,
        "interconnectors": flows,
        "tables": sorted(mms.tables),
    }


def observe_outage(rec: dict, market: dict | None, members: dict[str, list[str]]) -> dict:
    """Build the 'market observation' block for one outage. Never asserts causality."""
    if not market:
        return {"available": False, "note": "Cannot be confirmed yet: no public DispatchIS data has been retrieved."}
    binding_ids = {b["constraint_id"]: b for b in market.get("binding_constraints", [])}
    sets = []
    for cs in rec.get("constraint_sets", []):
        m = members.get(cs["id"])
        if m is None:
            sets.append({"id": cs["id"], "status": "unknown",
                         "note": "Cannot be confirmed: no member mapping (GENCONSET) for this constraint set."})
        else:
            hits = [binding_ids[c] for c in m if c in binding_ids]
            sets.append({"id": cs["id"], "status": "binding" if hits else "not_binding",
                         "members": len(m), "binding": hits})
    regions = rec.get("regions", [])
    adj = adjacent_interconnectors(regions)
    return {
        "available": True,
        "settlement_date": market.get("settlement_date"),
        "source_file": market.get("source_file"),
        "constraint_sets": sets,
        "prices": {r: market.get("prices", {}).get(r) for r in regions},
        "spreads": [s for s in market.get("spreads", []) if s["a"] in regions or s["b"] in regions],
        "interconnectors": {ic: market.get("interconnectors", {}).get(ic) for ic in adj},
        "note": "Public market data for the same dispatch interval, shown as a concurrent observation only. It does not establish that this outage caused any effect.",
    }
