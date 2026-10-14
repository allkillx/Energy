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

    def test_hh_aggregates_and_aligns_windows(self):
        v = [50.0] * q.DAY_INTERVALS
        for i in range(17 * 12 + 3, 19 * 12 + 3):  # 17:15-19:15 at $300
            v[i] = 300.0
        r = q.analyse_day(v, hh=True)
        # HH prices: 17:00 bucket = 175, 17:30..18:30 = 300, 19:00 bucket = 175
        self.assertAlmostEqual(r["top2h"], 268.75)
        self.assertEqual(r["top2h_start"], "17:00")
        self.assertAlmostEqual(r["eve_avg"], 175.0)

    def test_monthly_uses_median_ratio(self):
        rows = [{"date": f"2026-01-0{i}", "day_avg": 1.0, "top2h": t, "top4h": 1.0,
                 "ratio_2h_4h": t, "eve_top2h": 1.0, "eve_avg": 1.0, "ratio_eve": 1.0}
                for i, t in enumerate([1.0, 2.0, 9.0], 1)]
        jan, total = q.monthly(rows)
        self.assertEqual(jan["month"], "2026-01")
        self.assertAlmostEqual(jan["ratio_2h_4h_median"], 2.0)
        self.assertAlmostEqual(jan["top2h"], 4.0)
        self.assertEqual(total["days"], 3)

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
