"""Tests against a REAL NEOpoint export: NOS Planned Outages by Region, NSW1, from=2026-10-12, Daily."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from nos_monitor import neopoint, neopoint_nos
from nos_monitor.locations import Locations
from nos_monitor.nemweb import FetchError
from nos_monitor.outages import normalise
from nos_monitor.service import Monitor
from nos_monitor.store import Store
from nos_monitor.timeutil import parse_nem
from nos_monitor.validate import validate

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "neopoint", "nos_planned_outages_NSW1_from_2026-10-12.csv")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = parse_nem("2026-10-13 12:00:00")


def real_bytes() -> bytes:
    with open(FIXTURE, "rb") as fh:
        return fh.read()


class RealNosExportTests(unittest.TestCase):
    def setUp(self):
        self.rows = neopoint_nos.read_rows(real_bytes())
        self.mms = neopoint_nos.to_mms(self.rows)
        self.recs = {r["outage_id"]: r for r in normalise(self.mms, NOW)}

    def test_columns_dedup_and_counts(self):
        self.assertEqual(len(self.rows), 50)  # 51 data rows, one exact duplicate
        self.assertEqual(len(self.recs), 22)
        report = validate(self.mms, NOW)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["stats"]["status_codes_used"], ["COMPLETE", "PTP"])
        self.assertEqual(report["stats"]["status_codes_without_description"], [])
        self.assertEqual(report["stats"]["constraint_sets_distinct"], 24)
        self.assertEqual(report["stats"]["substations_without_region"], [])

    def test_status_times_and_constraint_sets(self):
        r = self.recs["998519"]
        self.assertEqual((r["status_code"], r["status_desc"]), ("COMPLETE", "Completed"))
        self.assertEqual((r["start"], r["end"]), ("2026-10-12T06:30:00+10:00", "2026-10-12T16:00:00+10:00"))
        self.assertIsNone(r["actual_start"])  # actual_* are per constraint set, not outage actuals
        sets = {c["id"]: c for c in r["constraint_sets"]}
        self.assertEqual(sorted(sets), ["I-QN_500", "N-ARTW_86", "N-DM_SVC", "N-MORBOR", "N-TW_SVC"])
        self.assertEqual(sets["I-QN_500"]["source_actual_start"], "2026-10-12T06:51:00+10:00")
        self.assertEqual(sets["N-ARTW_86"]["source_actual_start"], "2026-10-12T06:30:00+10:00")
        self.assertTrue(sets["I-QN_500"]["description"].startswith("Qld to NSW on QNI limited to 500 MW"))
        self.assertEqual(self.recs["1005437"]["constraint_sets"].__len__(), 4)

    def test_substations_equipment_and_regions(self):
        r = self.recs["1000049"]
        eq = {(e["substation_id"], e["equipment_type"], e["equipment_id"]) for e in r["equipment"]}
        self.assertEqual(eq, {("BALRANLD", "LINE", "X3"), ("BURONGA", "LINE", "X5/3")})
        self.assertEqual(r["regions"], ["NSW1"])
        temora = next(e for e in self.recs["1004351"]["equipment"] if e["equipment_id"] == "901")
        self.assertIn("TEMORA-WYA_SF 901", temora["equipment_desc"])
        self.assertIn("TEMORA-WWYAL_SF 901", temora["equipment_desc"])
        self.assertEqual(self.recs["1006238"]["equipment"][0]["owner"], "AusNet")
        self.assertEqual(self.recs["987362"]["recall_time_day"], "-1")

    def test_short_notice_outages_are_present_but_unclassified(self):
        late = [o for o, r in self.recs.items() if r["submitted"] > r["start"]]
        self.assertIn("1006835", late)  # submitted 15:17 for a 14:20 start
        self.assertIn("outage_type", neopoint_nos.NOT_PROVIDED)

    def test_format_changes_fail_loudly(self):
        bad = real_bytes().replace(b"genconsetid", b"constraintset", 1)
        with self.assertRaises(neopoint_nos.NeopointFormatError) as ctx:
            neopoint_nos.read_rows(bad)
        self.assertIn("genconsetid", str(ctx.exception))
        with self.assertRaises(neopoint_nos.NeopointFormatError):
            neopoint_nos.read_rows(b"<html><body>Login</body></html>")


class NeopointServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "nos.sqlite"))
        self.m = Monitor(self.store, Locations(os.path.join(ROOT, "data")), self.tmp,
                         enable_market=False, source="neopoint")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp)

    def test_import_real_file_and_state(self):
        res = self.m.import_neopoint_files([FIXTURE])
        self.assertTrue(res["new"])
        self.assertFalse(self.m.import_neopoint_files([FIXTURE])["new"])  # identical content not stored twice
        st = self.m.state()
        self.assertEqual(st["source"]["info"]["provider"], "NEOpoint")
        self.assertEqual(st["source"]["info"]["latest_submitted"], "2026-10-12T15:17:48+10:00")
        self.assertIn("reason", st["source"]["info"]["not_provided"])
        self.assertTrue(all(not o["mapped"] for o in st["outages"]))  # no verified coordinates yet
        feeds = {f["feed"]: f for f in st["feeds"]}
        self.assertTrue(feeds["nos_outages"]["status"].startswith("mapped"))
        self.assertTrue(feeds["constraints"]["status"].startswith("not available"))
        self.assertIn("manual import", " ".join(st["stale_reasons"]))

    def test_live_refresh_all_regions_or_nothing(self):
        data = real_bytes()
        env = {neopoint.KEY_ENV: "SECRETKEY123", "NEOPOINT_URL_NOS_OUTAGES": "https://n.example/?i={instances}&key={key}",
               "NEOPOINT_INSTANCES_NOS_OUTAGES": "NSW1,QLD1"}

        def fetch_ok(feed, instance=None):
            return data, f"https://n.example/?i={instance}&key=***"

        def fetch_qld_fails(feed, instance=None):
            if instance == "QLD1":
                raise FetchError("download", "https://n.example/?i=QLD1&key=***", "Tunnel connection failed: 403 Forbidden",
                                 status=403, via_proxy=True)
            return fetch_ok(feed, instance)

        with mock.patch.dict(os.environ, env), mock.patch.object(neopoint, "fetch", fetch_qld_fails):
            r = self.m.refresh(force=True)["network"]
        self.assertFalse(r["ok"])
        self.assertEqual(r["http_status"], 403)
        self.assertIsNone(self.store.latest_snapshot())  # partial data is never stored
        with mock.patch.dict(os.environ, env), mock.patch.object(neopoint, "fetch", fetch_ok):
            r = self.m.refresh(force=True)["network"]
        self.assertTrue(r["ok"] and r["new"], r)
        st = self.m.state()
        self.assertEqual(st["source"]["kind"], "neopoint")
        self.assertNotIn("SECRETKEY123", repr(st))
        self.assertNotIn("manual import", " ".join(st["stale_reasons"]))


if __name__ == "__main__":
    unittest.main()
