"""Ingest the 'Stow Hourly RG Auto' Custom Insights CSV.

Input  : data/stow_hourly_{date}.csv  (one row per reporting-group x hour x site)
Output : data/stow_hourly_{date}.json (per-hour revenue split Stowaway-proper vs
                                        Marilyna's, plus the dinner-window sums)

The CSV comes from the Lightspeed Custom Insights 'Sale Details' explore. Looker
prefixes column labels inconsistently ("Products Reporting Group Name" vs
"Reporting Group Name"), so columns are resolved by SUBSTRING match, not exact
name. If a required column can't be resolved we FAIL LOUD rather than guess
(ARCHITECTURE.md: fail toward review).

Money is Decimal. Revenue uses the line-level Gross Sale measures; the order-level
'Total Revenue' column is null at this grain and is ignored.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))

import config  # noqa: E402
from metrics import D, money  # noqa: E402

WINDOW_HOURS = {17, 18, 19, 20}   # 17:00-20:59 dinner window


def _resolve(headers, *needles):
    """Return the first header containing all needle substrings (case-insensitive)."""
    low = [(h, h.lower()) for h in headers]
    for h, hl in low:
        if all(n in hl for n in needles):
            return h
    raise SystemExit(
        "::error::hourly ingest: no column matching %s in headers %s"
        % (list(needles), headers))


def _num(raw):
    """Parse a Looker money cell to Decimal. Values arrive like '$1,176.70';
    strip the currency symbol and thousands separators. Blank -> 0."""
    s = (raw or "").replace("$", "").replace(",", "").strip()
    return D(s) if s else Decimal("0")


def ingest(csv_path, out_path):
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        col_rg = _resolve(headers, "reporting group", "name")
        col_hour = _resolve(headers, "hour")
        col_inc = _resolve(headers, "gross sale", "inc")
        col_ex = _resolve(headers, "gross sale", "ex")
        rows = list(reader)

    # scope -> hour -> {inc, ex}
    buckets = {"stowaway_proper": {}, "marilynas": {}}

    def add(scope, hour, inc, ex):
        b = buckets[scope].setdefault(hour, {"inc": Decimal("0"), "ex": Decimal("0")})
        b["inc"] += inc
        b["ex"] += ex

    for r in rows:
        rg = r.get(col_rg) or ""
        hour_raw = (r.get(col_hour) or "").strip()
        if not hour_raw:
            continue
        try:
            hour = int(float(hour_raw))
        except ValueError:
            continue
        inc = _num(r.get(col_inc))
        ex = _num(r.get(col_ex))
        if config.is_marilynas_row(rg):
            add("marilynas", hour, inc, ex)
        elif config.is_stowaway_proper_row(rg):
            add("stowaway_proper", hour, inc, ex)
        # HG-food-on-Stow-till ('harry gatos food') is intentionally dropped:
        # is_stowaway_proper_row returns False for it, is_marilynas_row False too.

    def summarise(scope):
        hours = buckets[scope]
        window_inc = sum((hours.get(h, {}).get("inc", Decimal("0")) for h in WINDOW_HOURS), Decimal("0"))
        window_ex = sum((hours.get(h, {}).get("ex", Decimal("0")) for h in WINDOW_HOURS), Decimal("0"))
        day_inc = sum((v["inc"] for v in hours.values()), Decimal("0"))
        return {
            "by_hour": {str(h): {"inc_gst": str(money(v["inc"])), "ex_gst": str(money(v["ex"]))}
                        for h, v in sorted(hours.items())},
            "window_1700_2059_inc_gst": str(money(window_inc)),
            "window_1700_2059_ex_gst": str(money(window_ex)),
            "day_total_inc_gst": str(money(day_inc)),
        }

    out = {
        "date": os.path.basename(csv_path).replace("stow_hourly_", "").replace(".csv", ""),
        "source_csv": os.path.basename(csv_path),
        "rows_ingested": len(rows),
        "stowaway_proper": summarise("stowaway_proper"),
        "marilynas": summarise("marilynas"),
    }
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print("wrote %s  (Stow window inc-GST %s, Mari window inc-GST %s)" % (
        out_path,
        out["stowaway_proper"]["window_1700_2059_inc_gst"],
        out["marilynas"]["window_1700_2059_inc_gst"]))
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: ingest_hourly.py <YYYY-MM-DD> [data_dir]")
    date = sys.argv[1]
    data_dir = sys.argv[2] if len(sys.argv) > 2 else "data"
    ingest(os.path.join(data_dir, "stow_hourly_%s.csv" % date),
           os.path.join(data_dir, "stow_hourly_%s.json" % date))
