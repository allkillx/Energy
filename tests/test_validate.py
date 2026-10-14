import glob
import os
import unittest

from nos_monitor import mms_csv, sample
from nos_monitor.outages import normalise, phase
from nos_monitor.reference import merge_lookups
from nos_monitor.timeutil import parse_nem
from nos_monitor.validate import format_report, validate

REAL_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "real")
REF_DIR = os.path.join(REAL_DIR, "reference")


def real_reference() -> mms_csv.MmsFile:
    """Real AEMO MMSDM 2026-09 lookup tables kept as fixtures (equipment detail omitted: 720 KB)."""
    merged = mms_csv.MmsFile()
    for path in sorted(glob.glob(os.path.join(REF_DIR, "*NETWORK_*.zip"))):
        merged.tables.update(mms_csv.parse_file(path).tables)
    return merged


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
                # The live file carries only these two tables (seen 2026-10-14); lookups come from the archive.
                self.assertIn("NETWORK_OUTAGEDETAIL", mms.tables)
                self.assertIn("NETWORK_OUTAGECONSTRAINTSET", mms.tables)
                added = merge_lookups(mms, real_reference())
                self.assertIn("NETWORK_SUBSTATIONDETAIL", added)
                report = validate(mms)
                self.assertTrue(report["ok"], format_report(report))
                st = report["stats"]
                self.assertGreater(st["records"], 0)
                self.assertEqual(st["status_codes_without_description"], [])
                # Measured 2026-10-14: 516/559 IDs resolve. The rest are mostly line-section codes
                # (275KV_813, 6C, N/A) plus sites newer than the archive month; they stay "Unknown".
                resolved = st["substations_used"] - len(st["substations_without_region"])
                self.assertGreaterEqual(resolved / st["substations_used"], 0.9)
                self.assertEqual(st["constraint_set_rows_without_outage"], 0)

    def test_real_file_times_with_milliseconds_and_withdrawn_status(self):
        files = sorted(glob.glob(os.path.join(REAL_DIR, "PUBLIC_NETWORK_*.zip")))
        if not files:
            self.skipTest("no real PUBLIC_NETWORK fixture in tests/fixtures/real/")
        mms = mms_csv.parse_file(files[-1])
        merge_lookups(mms, real_reference())
        now = parse_nem("2026-10-14 12:00:00")
        recs = normalise(mms, now)
        self.assertTrue(all(r["last_changed"] for r in recs))  # LASTCHANGED has .fff milliseconds
        withdrawn = [r for r in recs if r["status_code"] == "WDRAWN"]
        self.assertTrue(withdrawn)
        self.assertTrue(all(phase(r, now)[0] == "cancelled" for r in withdrawn))
        self.assertTrue(all(phase(r, now)[0] != "cancelled" for r in recs if r["status_code"] == "WD REQ"))


if __name__ == "__main__":
    unittest.main()
