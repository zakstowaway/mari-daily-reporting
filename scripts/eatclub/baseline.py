"""Pre-launch day-of-week baselines.

Two shapes, one per channel:

  dine-in (HG, Stowaway)  — mean offer-window revenue per weekday, from an hourly
                            window series. Stowaway's comes from the Custom
                            Insights 'Stow Hourly RG Auto' feed (Mari stripped by
                            RG); HG's from salesummarybyhour.

  takeaway (Marilyna's)   — mean delivery revenue per weekday, from
                            data/mari_daily_history.csv 'delivery_dollars'.

Baseline = the weeks BEFORE launch_date. Recomputing an old baseline must give the
same answer forever, so it is always pulled from immutable dated history, never a
rolling window.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal

from metrics import D, money

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_date(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def dow_baseline(rows, value_key, start, end, date_key="date"):
    """Generic DOW mean over [start, end] inclusive.

    rows: iterable of dicts. value_key: column to average. start/end: 'YYYY-MM-DD'.
    Returns {weekday_abbr: Decimal_mean}. Days with no data are omitted.
    """
    lo, hi = _parse_date(start), _parse_date(end)
    buckets = {d: [] for d in WEEKDAYS}
    for r in rows:
        try:
            d = _parse_date(r[date_key])
        except (KeyError, ValueError):
            continue
        if not (lo <= d <= hi):
            continue
        val = r.get(value_key)
        if val in (None, "", "None"):
            continue
        buckets[WEEKDAYS[d.weekday()]].append(D(val))
    out = {}
    for dow, vals in buckets.items():
        if vals:
            out[dow] = money(sum(vals) / len(vals))
    return out


def mari_delivery_baseline(history_csv_path, start, end):
    """Marilyna's pre-launch delivery baseline by DOW, ex-GST.

    Reads data/mari_daily_history.csv and averages 'delivery_dollars'
    (Uber Eats + own-driver) per weekday over the pre-launch window.
    """
    with open(history_csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    return dow_baseline(rows, "delivery_dollars", start, end)
