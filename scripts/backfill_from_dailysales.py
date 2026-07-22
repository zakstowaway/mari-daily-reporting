#!/usr/bin/env python3
"""Patch data/products_weekly.csv with rows from the OLD Daily Sales rollups
(`/Users/Shared/ClaudeShared/STOW/Daily Sales/Stowaway.csv` +
`HarryGatos.csv`) for any (venue, week_ending) combo that's clearly
under-covered vs adjacent weeks.

Why this exists
---------------

The API pipeline has three data sources with different windows:

* daily insights CSVs (`insights_*.csv`) — freshest, from Lightspeed emails
  via Pipedream. Coverage starts 2026-07-06.
* Looker product backfill (`data/looker_product_backfill.csv`) — historical.
  Its most-recent complete week ended 2026-06-28; its 2026-07-05 slice is
  partial because the Looker query was run mid-week.
* This script's OLD Daily Sales rollup — weekly-sales-pull skill's output,
  refreshed every Monday on Zak's Mac. Covers launch → last Sunday
  regardless of what Lightspeed's email format is doing.

Result: week ending 2026-07-05 has ~64 stow rows in the API vs ~520 in the
OLD rollup. This script closes that gap for any comparable week.

What it does
------------

1. Load current `data/products_weekly.csv`.
2. Compute per-(venue, week_ending) row count.
3. For each week_ending, if a venue's row count is under 40% of the median
   row count across all its other weeks, treat that (venue, week) as
   under-covered and REPLACE it with data aggregated from the OLD Daily
   Sales rollups.
4. Write back to `data/products_weekly.csv`.

Idempotent — safe to re-run. Only patches weeks that are demonstrably
sparse. Won't touch weeks that are already fine.

Run:
    python3 scripts/backfill_from_dailysales.py

Reads: data/products_weekly.csv,
       /Users/Shared/ClaudeShared/STOW/Daily Sales/Stowaway.csv,
       /Users/Shared/ClaudeShared/STOW/Daily Sales/HarryGatos.csv,
       scripts/product_dept_map.json
Writes: data/products_weekly.csv (in place)
"""
from __future__ import annotations

import csv
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PW_CSV = os.path.join(DATA, "products_weekly.csv")
DEPT_MAP_FILE = os.path.join(ROOT, "scripts", "product_dept_map.json")
BO_EXPORTS = os.path.join(DATA, "bo_exports")

# Both paths — the Mac's canonical location AND the Cowork mount path.
DAILY_SALES_CANDIDATES = [
    "/Users/Shared/ClaudeShared/STOW/Daily Sales",
    "/sessions/sweet-adoring-albattani/mnt/Daily Sales",
    os.path.expanduser("~/Documents/STOW/Daily Sales"),
]

PRODUCT_OVERRIDES = {"$60 BANQUET": "m"}
DEPT_VENUE = {"m": "mari", "hgf": "hg", "stf": "stow"}
UNMAPPED = {
    "f": "Kitchen (no reporting group)",
    "b": "Bar / FOH (no reporting group)",
    "m": "Marilyna's",
    "hgf": "Harry Gatos Food",
    "stf": "Stowaway Food",
}

_SIZE_SUFFIXES = {"pint", "schooner", "regular", "large", "bottle",
                  "glass", "large glass", "regular glass"}


def parse_num(x):
    s = str(x or "").strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "").replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def normalize_product(name):
    base, sep, suf = (name or "").rpartition(" - ")
    if sep:
        s = re.sub(r"\s*\[[^\]]*\]\s*$", "", suf).strip().lower()
        if s in _SIZE_SUFFIXES:
            return base.strip()
    return name


def week_ending(d):
    return d + timedelta(days=(6 - d.weekday()))


def find_daily_sales_dir() -> str:
    for p in DAILY_SALES_CANDIDATES:
        if os.path.exists(os.path.join(p, "Stowaway.csv")):
            return p
    raise SystemExit(
        "Could not find Daily Sales/. Tried: " + ", ".join(DAILY_SALES_CANDIDATES)
    )


def load_dept_map():
    with open(DEPT_MAP_FILE) as f:
        return json.load(f)


def dept_for(name, prefix, dmap):
    n = (name or "").strip()
    if prefix == "mari":
        return "f"
    if n in PRODUCT_OVERRIDES:
        return PRODUCT_OVERRIDES[n]
    vk = {"stow": "stow", "hg": "hg"}.get(prefix)
    return (dmap.get(vk, {}).get(n) or dmap.get("*", {}).get(n) or "b")


def load_rg_map() -> dict[str, str]:
    """Map product_name -> Lightspeed reporting group by reading the most recent
    bo_exports CSV. Missing exports -> empty map, and rows fall back to UNMAPPED."""
    rg_map: dict[str, str] = {}
    if not os.path.isdir(BO_EXPORTS):
        return rg_map
    exports = sorted(
        [f for f in os.listdir(BO_EXPORTS) if f.endswith(".csv")],
        reverse=True,
    )
    for fn in exports[:2]:  # newest 2 to catch cross-venue naming
        with open(os.path.join(BO_EXPORTS, fn), encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = (r.get("ProductName") or r.get("Product Name") or "").strip()
                rg = (r.get("ReportingGroupName") or r.get("Reporting Group") or "").strip()
                if name and rg:
                    rg_map.setdefault(name, rg)
    return rg_map


def load_products_weekly() -> list[dict]:
    with open(PW_CSV, newline="") as f:
        return list(csv.DictReader(f))


def coverage_stats(rows: list[dict]) -> dict[tuple[str, str], int]:
    """(venue, week_ending) -> row count."""
    cnt: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        cnt[(r["venue"], r["week_ending"])] += 1
    return cnt


def find_undercovered(cnt: dict[tuple[str, str], int]) -> list[tuple[str, str]]:
    """For each venue, flag any week whose row count is under 40% of the median
    of that venue's other week counts. Ignores the very first week per venue
    (venue open-date) which is naturally small."""
    by_venue: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (v, w), c in cnt.items():
        by_venue[v].append((w, c))
    flagged = []
    for v, wc in by_venue.items():
        wc.sort()
        earliest = wc[0][0]
        counts = [c for w, c in wc if w != earliest]
        if not counts:
            continue
        med = statistics.median(counts)
        threshold = 0.4 * med
        for w, c in wc:
            if w == earliest:
                continue
            if c < threshold:
                flagged.append((v, w))
                print(f"under-covered: {v} {w} has {c} rows vs median {med:.0f} (threshold {threshold:.0f})")
    return flagged


def read_daily_sales(daily_dir: str, prefix: str, dmap: dict) -> dict[tuple, list[float]]:
    """Aggregate OLD Daily Sales into (week_ending, venue, rg_placeholder,
    product) -> [ex_gst, qty]. Reporting group is left blank here (later step
    fills it from bo_exports or UNMAPPED[dept])."""
    fn = "Stowaway.csv" if prefix == "stow" else "HarryGatos.csv"
    path = os.path.join(daily_dir, fn)
    if not os.path.exists(path):
        print(f"skipping {fn}: not found in {daily_dir}")
        return {}
    agg = defaultdict(lambda: [0.0, 0.0])
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            date_str = (r.get("Date") or "").strip()
            if not date_str:
                continue
            try:
                d = date.fromisoformat(date_str[:10])
            except ValueError:
                continue
            we = week_ending(d).isoformat()
            name = (r.get("Product") or "").strip()
            if not name:
                continue
            qty = parse_num(r.get("Quantity"))
            sale_inc = parse_num(r.get("Sale Amount"))
            sale_ex = sale_inc / 1.1
            dept = dept_for(name, prefix, dmap)
            venue = DEPT_VENUE.get(dept, prefix)  # b/f falls through to till venue
            k = (we, venue, name)
            agg[k][0] += sale_ex
            agg[k][1] += qty
    return agg


def main():
    if not os.path.exists(PW_CSV):
        raise SystemExit(f"missing {PW_CSV} — run scripts/build_products_weekly.py first")

    rows = load_products_weekly()
    cnt = coverage_stats(rows)
    flagged = find_undercovered(cnt)
    if not flagged:
        print("no under-covered venue-weeks. products_weekly.csv is complete.")
        return

    flagged_set = set(flagged)
    daily_dir = find_daily_sales_dir()
    print(f"reading OLD Daily Sales from: {daily_dir}")

    dmap = load_dept_map()
    rg_map = load_rg_map()

    # Aggregate both till files
    stow_agg = read_daily_sales(daily_dir, "stow", dmap)
    hg_agg = read_daily_sales(daily_dir, "hg", dmap)

    # New rows only for flagged (venue, week) combos
    new_rows: list[dict] = []
    replacement_keys: set[tuple[str, str]] = set(flagged)
    for k, (ex, q) in {**stow_agg, **hg_agg}.items():
        we, venue, name = k
        if (venue, we) not in replacement_keys:
            continue
        rg = rg_map.get(name) or UNMAPPED.get(
            {"stow": "b", "hg": "b", "mari": "m"}.get(venue, "b"),
            "Unmapped",
        )
        new_rows.append(
            {
                "week_ending": we,
                "venue": venue,
                "reporting_group": rg,
                "product_name": name,
                "sales_ex_gst": round(ex, 2),
                "qty": round(q, 2),
                "cost": "0.0",
            }
        )

    if not new_rows:
        print("nothing to backfill — flagged weeks had no matching data in OLD Daily Sales.")
        return

    print(f"replacing {len(replacement_keys)} (venue, week) combos with {len(new_rows)} rows from OLD Daily Sales")

    # Filter OUT the flagged combos from the original rows, then append the new ones.
    kept = [r for r in rows if (r["venue"], r["week_ending"]) not in replacement_keys]
    dropped = len(rows) - len(kept)
    print(f"dropped {dropped} stale rows from flagged combos")

    combined = kept + new_rows
    # Sort deterministically
    combined.sort(key=lambda r: (r["week_ending"], r["venue"], r["reporting_group"], r["product_name"]))

    # Handle the case where the file has (or doesn't have) a `cost` column
    fieldnames = list(rows[0].keys()) if rows else [
        "week_ending", "venue", "reporting_group", "product_name", "sales_ex_gst", "qty", "cost"
    ]
    # Ensure new_rows have exactly the same keys
    for r in combined:
        for fn in fieldnames:
            r.setdefault(fn, "0.0" if fn == "cost" else "")

    with open(PW_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(combined)

    print(f"wrote {PW_CSV}: {len(combined)} rows total")


if __name__ == "__main__":
    main()
