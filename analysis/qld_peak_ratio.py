"""Daily QLD1 peak-price ratios from AEMO 5-minute price data.

For each NEM day (intervals ending 00:05 .. 24:00, NEM time) it reports:

- top2h:        highest average RRP over any contiguous 2-hour window in the day
- top4h:        highest average RRP over any contiguous 4-hour window in the day
- ratio_2h_4h:  top2h / top4h
- eve_top2h:    highest contiguous 2-hour average inside 16:00-20:00
- eve_avg:      average RRP over 16:00-20:00 (intervals ending 16:05 .. 20:00)
- ratio_eve:    eve_top2h / eve_avg

Data source: AEMO "Aggregated price and demand" monthly CSVs
(PRICE_AND_DEMAND_YYYYMM_QLD1.csv). Downloaded files are cached in --cache-dir;
files already there (e.g. downloaded by hand) are used without fetching.

    python analysis/qld_peak_ratio.py --year 2026 --out var/qld_peak_ratio_2026.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import urllib.request
from collections import defaultdict

URL = "https://aemo.com.au/aemo/data/nem/priceanddemand/PRICE_AND_DEMAND_{ym}_{region}.csv"
INTERVAL_MIN = 5
PER_HOUR = 60 // INTERVAL_MIN
DAY_INTERVALS = 24 * PER_HOUR


def month_file(cache_dir: str, ym: str, region: str, fetch: bool) -> str | None:
    path = os.path.join(cache_dir, f"PRICE_AND_DEMAND_{ym}_{region}.csv")
    if os.path.exists(path):
        return path
    if not fetch:
        return None
    req = urllib.request.Request(URL.format(ym=ym, region=region),
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
    except Exception as exc:  # month not published yet, network denied, ...
        print(f"skip {ym}: {exc}", file=sys.stderr)
        return None
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "wb") as f:
        f.write(body)
    return path


def read_prices(path: str) -> dict[dt.datetime, float]:
    """SETTLEMENTDATE (interval end, NEM time) -> RRP."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ts = dt.datetime.strptime(row["SETTLEMENTDATE"].strip(), "%Y/%m/%d %H:%M:%S")
            out[ts] = float(row["RRP"])
    return out


def group_days(prices: dict[dt.datetime, float]) -> dict[dt.date, list[float]]:
    """Complete days only: interval ending 00:00 belongs to the previous day."""
    days: dict[dt.date, dict[int, float]] = defaultdict(dict)
    for ts, rrp in prices.items():
        start = ts - dt.timedelta(minutes=INTERVAL_MIN)
        idx = (start.hour * 60 + start.minute) // INTERVAL_MIN
        days[start.date()][idx] = rrp
    return {d: [v[i] for i in range(DAY_INTERVALS)]
            for d, v in sorted(days.items()) if len(v) == DAY_INTERVALS}


def best_window(values: list[float], n: int) -> tuple[float, int]:
    """Highest mean over a contiguous window of n values -> (mean, start index)."""
    s = sum(values[:n])
    best, at = s, 0
    for i in range(n, len(values)):
        s += values[i] - values[i - n]
        if s > best:
            best, at = s, i - n + 1
    return best / n, at


def hhmm(idx: int) -> str:
    m = idx * INTERVAL_MIN
    return f"{m // 60:02d}:{m % 60:02d}"


def ratio(a: float, b: float) -> float | None:
    return a / b if b > 0 else None  # undefined when the base is zero/negative


def analyse_day(v: list[float]) -> dict:
    top2, at2 = best_window(v, 2 * PER_HOUR)
    top4, at4 = best_window(v, 4 * PER_HOUR)
    eve_lo, eve_hi = 16 * PER_HOUR, 20 * PER_HOUR
    eve = v[eve_lo:eve_hi]
    eve2, ate = best_window(eve, 2 * PER_HOUR)
    eve_avg = sum(eve) / len(eve)
    return {
        "top2h": top2, "top2h_start": hhmm(at2),
        "top4h": top4, "top4h_start": hhmm(at4),
        "ratio_2h_4h": ratio(top2, top4),
        "eve_top2h": eve2, "eve_top2h_start": hhmm(eve_lo + ate),
        "eve_avg": eve_avg,
        "ratio_eve": ratio(eve2, eve_avg),
        "day_avg": sum(v) / len(v),
    }


FIELDS = ["date", "day_avg", "top2h", "top2h_start", "top4h", "top4h_start", "ratio_2h_4h",
          "eve_top2h", "eve_top2h_start", "eve_avg", "ratio_eve"]


def fmt(x):
    return "" if x is None else (f"{x:.4f}" if isinstance(x, float) else x)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--region", default="QLD1")
    p.add_argument("--cache-dir", default="var/aemo_price")
    p.add_argument("--no-fetch", action="store_true", help="only use files already in --cache-dir")
    p.add_argument("--out", default=None, help="daily CSV output (default: stdout)")
    a = p.parse_args(argv)

    today = dt.date.today()
    last_month = 12 if a.year < today.year else today.month
    prices: dict[dt.datetime, float] = {}
    for m in range(1, last_month + 1):
        path = month_file(a.cache_dir, f"{a.year}{m:02d}", a.region, not a.no_fetch)
        if path:
            prices.update(read_prices(path))
    # Jan 1 00:00 interval end lives in the next month's file; fetch it if available.
    nxt = f"{a.year + 1}01" if last_month == 12 else None
    if nxt and (path := month_file(a.cache_dir, nxt, a.region, not a.no_fetch)):
        prices.update(read_prices(path))

    days = {d: v for d, v in group_days(prices).items() if d.year == a.year}
    if not days:
        print("no complete days found", file=sys.stderr)
        return 1
    rows = [{"date": d.isoformat(), **analyse_day(v)} for d, v in days.items()]

    out = open(a.out, "w", newline="") if a.out else sys.stdout
    w = csv.DictWriter(out, FIELDS)
    w.writeheader()
    for r in rows:
        w.writerow({k: fmt(r[k]) for k in FIELDS})
    if a.out:
        out.close()

    def mean(key):
        xs = [r[key] for r in rows if r[key] is not None]
        return sum(xs) / len(xs) if xs else float("nan")

    print(f"\n{a.region} {a.year}: {len(rows)} complete days "
          f"({rows[0]['date']} .. {rows[-1]['date']})", file=sys.stderr)
    for key in ("top2h", "top4h", "ratio_2h_4h", "eve_top2h", "eve_avg", "ratio_eve"):
        print(f"  mean {key:<12} {mean(key):10.3f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
