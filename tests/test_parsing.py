import io
import os
import unittest
import zipfile
from datetime import datetime

from nos_monitor import mms_csv, nemweb, sample
from nos_monitor.diff import diff_snapshots
from nos_monitor.outages import annotate, normalise, phase
from nos_monitor.timeutil import NEM_TZ, parse_nem

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=NEM_TZ)


class MmsCsvTests(unittest.TestCase):
    def test_parses_tables_from_i_rows(self):
        text = (
            'C,NEMP.WORLD,PUBLIC_NETWORK,AEMO,PUBLIC,2026/10/12,12:30:05,0000000500000021,NETWORK,0000000500000021\n'
            'I,NETWORK,OUTAGEDETAIL,3,OUTAGEID,SUBSTATIONID,EQUIPMENTTYPE,EQUIPMENTID,STARTTIME,ENDTIME,REASON\n'
            'D,NETWORK,OUTAGEDETAIL,3,123,SUBA,LINE,L1,"2026/10/12 10:00:00","2026/10/12 18:00:00","Maint, with comma"\n'
            'I,NETWORK,OUTAGECONSTRAINTSET,1,OUTAGEID,GENCONSETID,STARTINTERVAL,ENDINTERVAL\n'
            'D,NETWORK,OUTAGECONSTRAINTSET,1,123,N-X_SET,"2026/10/12 10:00:00","2026/10/12 18:00:00"\n'
            'C,"END OF REPORT",6\n'
        )
        mms = mms_csv.parse_text(text)
        self.assertEqual(mms.report_date, "2026/10/12 12:30:05")
        self.assertEqual(set(mms.tables), {"NETWORK_OUTAGEDETAIL", "NETWORK_OUTAGECONSTRAINTSET"})
        row = mms.rows("NETWORK_OUTAGEDETAIL")[0]
        self.assertEqual(row["REASON"], "Maint, with comma")
        self.assertEqual(row["STARTTIME"], "2026/10/12 10:00:00")
        self.assertEqual(mms.table("network_outagedetail").version, "3")
        self.assertEqual(mms.warnings, [])

    def test_extra_and_missing_columns_warn_but_parse(self):
        text = "I,NETWORK,OUTAGEDETAIL,3,A,B\nD,NETWORK,OUTAGEDETAIL,3,1\nD,NETWORK,OUTAGEDETAIL,3,1,2,3\n"
        mms = mms_csv.parse_text(text)
        rows = mms.rows("NETWORK_OUTAGEDETAIL")
        self.assertEqual(rows[0], {"A": "1", "B": ""})
        self.assertEqual(len(mms.warnings), 2)

    def test_zip_round_trip(self):
        name, text = sample.network_csv(1, NOW)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(name, text)
        mms = mms_csv.parse_zip_bytes(buf.getvalue())
        self.assertIn("NETWORK_OUTAGEDETAIL", mms.tables)
        self.assertGreater(len(mms.rows("NETWORK_OUTAGEDETAIL")), 0)


class ListingTests(unittest.TestCase):
    def test_latest_network_file_is_chosen_dynamically(self):
        with open(os.path.join(FIXTURES, "network_listing.html")) as fh:
            entries = nemweb.parse_listing(fh.read(), nemweb.NETWORK_URL)
        names = [e.name for e in entries]
        self.assertNotIn("DUPLICATE", names)
        latest = nemweb.latest(entries, "PUBLIC_NETWORK_")
        self.assertEqual(latest.name, "PUBLIC_NETWORK_202610120900_0000000500000020.zip")
        self.assertEqual(latest.url, "https://nemweb.com.au/Reports/Current/Network/" + latest.name)
        self.assertEqual(latest.name_time, datetime(2026, 10, 12, 9, 0, tzinfo=NEM_TZ))
        self.assertEqual(latest.listing_time, datetime(2026, 10, 12, 9, 1, tzinfo=NEM_TZ))
        self.assertEqual(latest.size, 48555)

    def test_no_match_raises(self):
        with self.assertRaises(nemweb.FetchError) as ctx:
            nemweb.find_latest("https://x/", "PUBLIC_NETWORK_", getter=lambda url: b"<html></html>")
        self.assertEqual(ctx.exception.stage, "listing")
        self.assertEqual(ctx.exception.url, "https://x/")

    def test_http_errors_keep_url_status_and_stage(self):
        import urllib.error
        from unittest import mock

        def boom(*a, **k):
            raise urllib.error.URLError("Tunnel connection failed: 403 Forbidden")

        with mock.patch("urllib.request.urlopen", boom):
            with self.assertRaises(nemweb.FetchError) as ctx:
                nemweb.find_latest(nemweb.NETWORK_URL, "PUBLIC_NETWORK_")
        e = ctx.exception
        self.assertEqual((e.stage, e.url, e.status, e.via_proxy), ("listing", nemweb.NETWORK_URL, 403, True))
        self.assertIn("HTTP 403 from proxy CONNECT", str(e))

        def not_found(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        entry = nemweb.ListingEntry("PUBLIC_NETWORK_202610120900_01.zip", nemweb.NETWORK_URL + "x.zip", None, None)
        with mock.patch("urllib.request.urlopen", not_found):
            with self.assertRaises(nemweb.FetchError) as ctx:
                nemweb.download(entry)
        self.assertEqual((ctx.exception.stage, ctx.exception.status), ("download", 404))


class OutageTests(unittest.TestCase):
    def setUp(self):
        self.mms = mms_csv.parse_text(sample.network_csv(1, NOW)[1])
        self.records = {r["outage_id"]: r for r in normalise(self.mms, NOW)}

    def test_groups_equipment_and_joins_lookups(self):
        r = self.records["DEMO-1001"]
        self.assertEqual(len(r["equipment"]), 2)
        self.assertEqual(r["regions"], ["NSW1"])
        self.assertEqual(r["equipment"][0]["substation_name"], "Demo NSW substation A")
        self.assertEqual(r["equipment"][0]["voltage"], "330")
        self.assertEqual(r["status_desc"], "In progress (sample description)")
        self.assertEqual([c["id"] for c in r["constraint_sets"]], ["DEMO-N-SET1"])
        self.assertEqual(r["start"], "2026-10-12T09:00:00+10:00")

    def test_phase_classification(self):
        self.assertEqual(phase(self.records["DEMO-1001"], NOW), ("active", "actual"))
        self.assertEqual(phase(self.records["DEMO-1002"], NOW), ("upcoming", "planned"))
        self.assertEqual(phase(self.records["DEMO-1006"], NOW), ("ended", "actual"))
        self.assertEqual(phase({"status_code": "WITHDRAWN"}, NOW), ("cancelled", "status"))
        self.assertEqual(phase({"start": "2026-10-12T11:00:00+10:00", "end": "2026-10-12T13:00:00+10:00"}, NOW),
                         ("active", "planned"))

    def test_attention_rules(self):
        recs = list(self.records.values())
        annotate(recs, NOW, {"DEMO-1005"}, {"DEMO_VIC_A": ["VIC1-NSW1"]})
        by = {r["outage_id"]: r for r in recs}
        self.assertTrue(by["DEMO-1001"]["attention"])           # active + constraint set
        self.assertTrue(by["DEMO-1002"]["attention"])           # starts within 24h + constraint set
        self.assertFalse(by["DEMO-1003"]["attention"])          # 48h away, though interconnector-mapped
        self.assertEqual(by["DEMO-1003"]["interconnectors"], ["VIC1-NSW1"])
        self.assertTrue(by["DEMO-1005"]["attention"])           # changed in latest diff
        self.assertFalse(by["DEMO-1006"]["attention"])          # ended

    def test_nem_time_parsing(self):
        self.assertEqual(parse_nem("2026/10/12 10:00:00").utcoffset().total_seconds(), 36000)
        self.assertIsNone(parse_nem(""))


class DiffTests(unittest.TestCase):
    def test_all_change_kinds(self):
        a = normalise(mms_csv.parse_text(sample.network_csv(1, NOW)[1]), NOW)
        b = normalise(mms_csv.parse_text(sample.network_csv(2, NOW)[1]), NOW)
        events = diff_snapshots(a, b)
        kinds = {(e["outage_id"], e["kind"]) for e in events}
        self.assertIn(("DEMO-1008", "added"), kinds)
        self.assertIn(("DEMO-1007", "removed"), kinds)
        self.assertIn(("DEMO-1002", "time"), kinds)
        self.assertIn(("DEMO-1003", "status"), kinds)
        self.assertIn(("DEMO-1001", "constraint_sets"), kinds)
        self.assertEqual(diff_snapshots(a, a), [])
        removed = next(e for e in events if e["kind"] == "removed")
        self.assertEqual(removed["regions"], ["TAS1"])


if __name__ == "__main__":
    unittest.main()
