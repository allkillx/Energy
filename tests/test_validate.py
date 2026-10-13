import glob
import os
import unittest

from nos_monitor import mms_csv, sample
from nos_monitor.validate import format_report, validate

REAL_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "real")


class ValidateSampleTests(unittest.TestCase):
    def test_sample_file_is_valid(self):
        report = validate(mms_csv.parse_text(sample.network_csv(1)[1]))
        self.assertTrue(report["ok"], format_report(report))
        st = report["stats"]
        self.assertEqual(st["outage_ids"], 7)
        self.assertEqual(st["constraint_set_rows"], 3)
        self.assertEqual(st["status_codes_without_description"], [])
        self.assertEqual(st["records_with_constraint_sets"], 3)

    def test_missing_status_column_is_an_error(self):
        text = sample.network_csv(1)[1].replace("OUTAGESTATUSCODE,RESUBMITREASON", "XSTATUS,RESUBMITREASON")
        report = validate(mms_csv.parse_text(text))
        self.assertFalse(report["ok"])
        self.assertTrue(any("OUTAGESTATUSCODE" in e for e in report["errors"]))

    def test_unknown_status_code_and_orphan_constraint_set_warn(self):
        text = sample.network_csv(1)[1].replace(",PROGRESSING,", ",NEWCODE,", 1)
        text = text.replace("D,NETWORK,OUTAGECONSTRAINTSET,1,DEMO-1004", "D,NETWORK,OUTAGECONSTRAINTSET,1,NOPE-9")
        report = validate(mms_csv.parse_text(text))
        self.assertTrue(report["ok"])
        self.assertIn("NEWCODE", report["stats"]["status_codes_without_description"])
        self.assertEqual(report["stats"]["constraint_set_rows_without_outage"], 1)


class RealFileTests(unittest.TestCase):
    """Runs against real AEMO files saved by `python -m nos_monitor diagnose --save-fixture`.

    Skipped when tests/fixtures/real/ holds no PUBLIC_NETWORK_*.zip (e.g. no NEMweb access).
    """

    def test_real_public_network_files(self):
        files = sorted(glob.glob(os.path.join(REAL_DIR, "PUBLIC_NETWORK_*.zip")))
        if not files:
            self.skipTest("no real PUBLIC_NETWORK fixture in tests/fixtures/real/")
        for path in files:
            with self.subTest(file=os.path.basename(path)):
                mms = mms_csv.parse_file(path)
                report = validate(mms)
                self.assertTrue(report["ok"], format_report(report))
                self.assertGreater(report["stats"]["records"], 0)
                self.assertIn("NETWORK_OUTAGESTATUSCODE", report["stats"]["tables"])
                self.assertIn("NETWORK_OUTAGECONSTRAINTSET", report["stats"]["tables"])


if __name__ == "__main__":
    unittest.main()
