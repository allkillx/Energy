import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))
import qld_peak_ratio as q  # noqa: E402


class PeakRatioTest(unittest.TestCase):
    def test_flat_day_with_evening_spike(self):
        v = [50.0] * q.DAY_INTERVALS
        for i in range(17 * 12, 19 * 12):  # 17:00-19:00 at $300
            v[i] = 300.0
        r = q.analyse_day(v)
        self.assertAlmostEqual(r["top2h"], 300.0)
        self.assertEqual(r["top2h_start"], "17:00")
        self.assertAlmostEqual(r["top4h"], 175.0)
        self.assertAlmostEqual(r["ratio_2h_4h"], 300 / 175)
        self.assertAlmostEqual(r["eve_top2h"], 300.0)
        self.assertAlmostEqual(r["eve_avg"], 175.0)

    def test_negative_base_gives_no_ratio(self):
        r = q.analyse_day([-10.0] * q.DAY_INTERVALS)
        self.assertIsNone(r["ratio_2h_4h"])

    def test_group_days_assigns_midnight_to_previous_day(self):
        import datetime as dt
        start = dt.datetime(2026, 1, 1, 0, 5)
        prices = {start + dt.timedelta(minutes=5 * i): float(i) for i in range(q.DAY_INTERVALS)}
        days = q.group_days(prices)
        self.assertEqual(list(days), [dt.date(2026, 1, 1)])


if __name__ == "__main__":
    unittest.main()
