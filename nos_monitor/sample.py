"""Synthetic sample files in MMS CSV format for the demo and tests.

Everything here is fabricated (IDs start with DEMO_). Column layouts follow the
MMS Data Model tables but must be checked against a real PUBLIC_NETWORK file
(`python -m nos_monitor inspect <zip>`).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

from .timeutil import now_nem

OUTAGE_COLS = ["OUTAGEID", "SUBSTATIONID", "EQUIPMENTTYPE", "EQUIPMENTID", "STARTTIME", "ENDTIME",
               "SUBMITTEDDATE", "OUTAGESTATUSCODE", "RESUBMITREASON", "RESUBMITOUTAGEID", "RECALLTIMEDAY",
               "RECALLTIMENIGHT", "LASTCHANGED", "REASON", "ISSECONDARY", "ACTUAL_STARTTIME", "ACTUAL_ENDTIME",
               "COMPANYREFCODE"]
CONSET_COLS = ["OUTAGEID", "GENCONSETID", "STARTINTERVAL", "ENDINTERVAL"]
STATUS_COLS = ["OUTAGESTATUSCODE", "DESCRIPTION", "LASTCHANGED"]
SUB_COLS = ["SUBSTATIONID", "VALIDFROM", "VALIDTO", "DESCRIPTION", "REGIONID", "OWNERID", "LASTCHANGED"]
EQ_COLS = ["SUBSTATIONID", "EQUIPMENTTYPE", "EQUIPMENTID", "VALIDFROM", "VALIDTO", "VOLTAGE", "DESCRIPTION",
           "LASTCHANGED"]

SUBSTATIONS = [
    ("DEMO_NSW_A", "Demo NSW substation A", "NSW1"),
    ("DEMO_NSW_B", "Demo NSW substation B", "NSW1"),
    ("DEMO_QLD_A", "Demo QLD substation A", "QLD1"),
    ("DEMO_QLD_B", "Demo QLD substation B", "QLD1"),
    ("DEMO_VIC_A", "Demo VIC substation A", "VIC1"),
    ("DEMO_VIC_B", "Demo VIC substation B", "VIC1"),
    ("DEMO_SA_A", "Demo SA substation A", "SA1"),
    ("DEMO_TAS_A", "Demo TAS substation A", "TAS1"),
    ("DEMO_NSW_UNMAPPED", "Demo NSW substation without coordinates", "NSW1"),
]


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y/%m/%d %H:%M:%S") if dt else ""


def _write(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerows(rows)
    return buf.getvalue()


def _table(rows, report, sub, version, cols, data):
    rows.append(["I", report, sub, str(version), *cols])
    for d in data:
        rows.append(["D", report, sub, str(version), *[d.get(c, "") for c in cols]])


def network_csv(version: int = 1, now: datetime | None = None) -> tuple[str, str]:
    """Return (filename, csv_text). version=2 modifies version 1 to exercise every diff kind."""
    now = (now or now_nem()).replace(second=0, microsecond=0)
    pub = now.replace(minute=0 if now.minute < 30 else 30)
    if version == 1:
        pub -= timedelta(minutes=30)
    h = lambda hours: now + timedelta(hours=hours)  # noqa: E731
    lc = _fmt(pub)
    outages = [
        dict(OUTAGEID="DEMO-1001", SUBSTATIONID="DEMO_NSW_A", EQUIPMENTTYPE="LINE", EQUIPMENTID="DEMO_L1",
             STARTTIME=_fmt(h(-3)), ENDTIME=_fmt(h(5)), ACTUAL_STARTTIME=_fmt(h(-3)), OUTAGESTATUSCODE="PROGRESSING",
             REASON="Maintenance (sample)", ISSECONDARY="0"),
        dict(OUTAGEID="DEMO-1001", SUBSTATIONID="DEMO_NSW_B", EQUIPMENTTYPE="LINE", EQUIPMENTID="DEMO_L1",
             STARTTIME=_fmt(h(-3)), ENDTIME=_fmt(h(5)), ACTUAL_STARTTIME=_fmt(h(-3)), OUTAGESTATUSCODE="PROGRESSING",
             REASON="Maintenance (sample)", ISSECONDARY="0"),
        dict(OUTAGEID="DEMO-1002", SUBSTATIONID="DEMO_QLD_A", EQUIPMENTTYPE="TRANS", EQUIPMENTID="DEMO_T2",
             STARTTIME=_fmt(h(6)), ENDTIME=_fmt(h(30)), OUTAGESTATUSCODE="SUBMITTED", REASON="Testing (sample)",
             ISSECONDARY="0"),
        dict(OUTAGEID="DEMO-1003", SUBSTATIONID="DEMO_VIC_A", EQUIPMENTTYPE="CB", EQUIPMENTID="DEMO_CB7",
             STARTTIME=_fmt(h(48)), ENDTIME=_fmt(h(56)), OUTAGESTATUSCODE="SUBMITTED", REASON="Upgrade (sample)",
             ISSECONDARY="1"),
        dict(OUTAGEID="DEMO-1004", SUBSTATIONID="DEMO_SA_A", EQUIPMENTTYPE="CAP", EQUIPMENTID="DEMO_C1",
             STARTTIME=_fmt(h(-1)), ENDTIME=_fmt(h(2)), OUTAGESTATUSCODE="PROGRESSING", REASON="Repair (sample)",
             ISSECONDARY="0"),
        dict(OUTAGEID="DEMO-1005", SUBSTATIONID="DEMO_NSW_UNMAPPED", EQUIPMENTTYPE="REAC", EQUIPMENTID="DEMO_R3",
             STARTTIME=_fmt(h(10)), ENDTIME=_fmt(h(14)), OUTAGESTATUSCODE="SUBMITTED", REASON="Inspection (sample)",
             ISSECONDARY="0"),
        dict(OUTAGEID="DEMO-1006", SUBSTATIONID="DEMO_VIC_B", EQUIPMENTTYPE="LINE", EQUIPMENTID="DEMO_L9",
             STARTTIME=_fmt(h(-30)), ENDTIME=_fmt(h(-20)), ACTUAL_STARTTIME=_fmt(h(-30)),
             ACTUAL_ENDTIME=_fmt(h(-21)), OUTAGESTATUSCODE="COMPLETED", REASON="Maintenance (sample)",
             ISSECONDARY="0"),
        dict(OUTAGEID="DEMO-1007", SUBSTATIONID="DEMO_TAS_A", EQUIPMENTTYPE="BUS", EQUIPMENTID="DEMO_B1",
             STARTTIME=_fmt(h(20)), ENDTIME=_fmt(h(26)), OUTAGESTATUSCODE="SUBMITTED", REASON="Works (sample)",
             ISSECONDARY="0"),
    ]
    consets = [
        dict(OUTAGEID="DEMO-1001", GENCONSETID="DEMO-N-SET1", STARTINTERVAL=_fmt(h(-3)), ENDINTERVAL=_fmt(h(5))),
        dict(OUTAGEID="DEMO-1002", GENCONSETID="DEMO-Q-SET2", STARTINTERVAL=_fmt(h(6)), ENDINTERVAL=_fmt(h(30))),
        dict(OUTAGEID="DEMO-1004", GENCONSETID="DEMO-S-SET4", STARTINTERVAL=_fmt(h(-1)), ENDINTERVAL=_fmt(h(2))),
    ]
    if version == 2:
        outages = [o for o in outages if o["OUTAGEID"] != "DEMO-1007"]  # removed
        for o in outages:
            if o["OUTAGEID"] == "DEMO-1002":
                o["STARTTIME"] = _fmt(h(4))  # time change
            if o["OUTAGEID"] == "DEMO-1003":
                o["OUTAGESTATUSCODE"] = "WITHDRAWN"  # status change
        outages.append(dict(OUTAGEID="DEMO-1008", SUBSTATIONID="DEMO_QLD_B", EQUIPMENTTYPE="LINE",
                            EQUIPMENTID="DEMO_L4", STARTTIME=_fmt(h(3)), ENDTIME=_fmt(h(9)),
                            OUTAGESTATUSCODE="SUBMITTED", REASON="Urgent repair (sample)", ISSECONDARY="0"))
        consets.append(dict(OUTAGEID="DEMO-1008", GENCONSETID="DEMO-Q-SET8", STARTINTERVAL=_fmt(h(3)),
                            ENDINTERVAL=_fmt(h(9))))
        consets.append(dict(OUTAGEID="DEMO-1001", GENCONSETID="DEMO-N-SET1B", STARTINTERVAL=_fmt(h(-3)),
                            ENDINTERVAL=_fmt(h(5))))  # constraint set change
    for o in outages:
        o.setdefault("SUBMITTEDDATE", _fmt(h(-72)))
        o.setdefault("LASTCHANGED", lc)
        o.setdefault("COMPANYREFCODE", "SAMPLE")
    statuses = [dict(OUTAGESTATUSCODE=c, DESCRIPTION=d, LASTCHANGED=lc) for c, d in [
        ("SUBMITTED", "Submitted (sample description)"), ("PROGRESSING", "In progress (sample description)"),
        ("COMPLETED", "Completed (sample description)"), ("WITHDRAWN", "Withdrawn (sample description)")]]
    subs = [dict(SUBSTATIONID=s, VALIDFROM="2020/01/01 00:00:00", DESCRIPTION=d, REGIONID=r, OWNERID="DEMO_TNSP",
                 LASTCHANGED=lc) for s, d, r in SUBSTATIONS]
    eqs = [dict(SUBSTATIONID=o["SUBSTATIONID"], EQUIPMENTTYPE=o["EQUIPMENTTYPE"], EQUIPMENTID=o["EQUIPMENTID"],
                VALIDFROM="2020/01/01 00:00:00", VOLTAGE="330", DESCRIPTION=f"Demo {o['EQUIPMENTID']}",
                LASTCHANGED=lc) for o in outages]
    rows = [["C", "NEMP.WORLD", "PUBLIC_NETWORK", "AEMO", "PUBLIC", pub.strftime("%Y/%m/%d"),
             pub.strftime("%H:%M:%S"), "0000000000000001", "SAMPLE", "0000000000000001"]]
    _table(rows, "NETWORK", "OUTAGEDETAIL", 3, OUTAGE_COLS, outages)
    _table(rows, "NETWORK", "OUTAGECONSTRAINTSET", 1, CONSET_COLS, consets)
    _table(rows, "NETWORK", "OUTAGESTATUSCODE", 1, STATUS_COLS, statuses)
    _table(rows, "NETWORK", "SUBSTATIONDETAIL", 2, SUB_COLS, subs)
    _table(rows, "NETWORK", "EQUIPMENTDETAIL", 2, EQ_COLS, eqs)
    rows.append(["C", "END OF REPORT", str(len(rows) + 1)])
    return f"PUBLIC_NETWORK_SAMPLE_{pub.strftime('%Y%m%d%H%M')}_0000000000000001.CSV", _write(rows)


def dispatch_csv(now: datetime | None = None) -> tuple[str, str]:
    now = (now or now_nem()).replace(second=0, microsecond=0)
    sd = now - timedelta(minutes=now.minute % 5)
    s = _fmt(sd)
    rows = [["C", "NEMP.WORLD", "DISPATCHIS", "AEMO", "PUBLIC", sd.strftime("%Y/%m/%d"), sd.strftime("%H:%M:%S"),
             "0000000000000002", "SAMPLE", "0000000000000002"]]
    _table(rows, "DISPATCH", "CONSTRAINT", 5,
           ["SETTLEMENTDATE", "RUNNO", "CONSTRAINTID", "DISPATCHINTERVAL", "INTERVENTION", "RHS", "MARGINALVALUE",
            "VIOLATIONDEGREE", "LHS"],
           [dict(SETTLEMENTDATE=s, RUNNO="1", CONSTRAINTID="DEMO_N>>L1_A", INTERVENTION="0", RHS="500",
                 MARGINALVALUE="-35.2", VIOLATIONDEGREE="0", LHS="500"),
            dict(SETTLEMENTDATE=s, RUNNO="1", CONSTRAINTID="DEMO_Q>>T2_B", INTERVENTION="0", RHS="800",
                 MARGINALVALUE="0", VIOLATIONDEGREE="0", LHS="640"),
            dict(SETTLEMENTDATE=s, RUNNO="1", CONSTRAINTID="DEMO_OTHER_X", INTERVENTION="0", RHS="100",
                 MARGINALVALUE="-4.1", VIOLATIONDEGREE="0", LHS="100")])
    _table(rows, "DISPATCH", "PRICE", 5,
           ["SETTLEMENTDATE", "RUNNO", "REGIONID", "DISPATCHINTERVAL", "INTERVENTION", "RRP"],
           [dict(SETTLEMENTDATE=s, RUNNO="1", REGIONID=r, INTERVENTION="0", RRP=p)
            for r, p in [("NSW1", "112.50"), ("QLD1", "84.10"), ("VIC1", "61.00"), ("SA1", "95.30"), ("TAS1", "58.70")]])
    _table(rows, "DISPATCH", "INTERCONNECTORRES", 3,
           ["SETTLEMENTDATE", "RUNNO", "INTERCONNECTORID", "DISPATCHINTERVAL", "INTERVENTION", "METEREDMWFLOW",
            "MWFLOW", "MWLOSSES", "MARGINALVALUE", "VIOLATIONDEGREE", "EXPORTLIMIT", "IMPORTLIMIT"],
           [dict(SETTLEMENTDATE=s, RUNNO="1", INTERCONNECTORID=i, INTERVENTION="0", METEREDMWFLOW=f, MWFLOW=f,
                 MWLOSSES="5", MARGINALVALUE=m, VIOLATIONDEGREE="0", EXPORTLIMIT=e, IMPORTLIMIT=im)
            for i, f, m, e, im in [("NSW1-QLD1", "-420", "0", "600", "-1000"), ("N-Q-MNSP1", "-80", "0", "100", "-200"),
                                   ("VIC1-NSW1", "870", "-12.5", "870", "-1100"), ("V-SA", "210", "0", "600", "-550"),
                                   ("V-S-MNSP1", "90", "0", "220", "-200"), ("T-V-MNSP1", "300", "0", "478", "-478")]])
    rows.append(["C", "END OF REPORT", str(len(rows) + 1)])
    return f"PUBLIC_DISPATCHIS_SAMPLE_{sd.strftime('%Y%m%d%H%M')}_0000000000000002.CSV", _write(rows)


GENCONSET_SAMPLE = [("DEMO-N-SET1", "DEMO_N>>L1_A"), ("DEMO-Q-SET2", "DEMO_Q>>T2_B")]
