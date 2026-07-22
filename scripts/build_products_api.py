#!/usr/bin/env python3
"""Build the product-level Sales API served from app.stowawaybar.com.

Reads data/products_weekly.csv (built by build_products_weekly.py) and emits
JSON endpoints under dashboard/sales/products/ so a Claude session on any
surface (web/mobile) can query product-level history without a local clone.

Emitted files:

    dashboard/sales/products/
        rollup_stow.json    per-product weekly detail for Stowaway
        rollup_hg.json      per-product weekly detail for Harry Gatos
        rollup_mari.json    per-product weekly detail for Marilyna's
        index.json          lean list of every product with lifetime stats
        latest.json         freshness stamp + coverage counts

Deploy: commit the emitted JSONs; GitHub Pages serves them at
    https://app.stowawaybar.com/sales/products/<file>.json

All revenue figures are ex-GST. Week endings are ISO dates (Sunday of the
Mon-Sun trading week). Stowaway is closed Mondays; Harry Gatos is closed
Tuesdays; Marilyna's has no till of its own — its revenue is the 'm'
attribution slice of the Stowaway till, already resolved by the underlying
products_weekly.csv build.

Run:
    python3 scripts/build_products_api.py

Idempotent — safe to re-run on every daily pull. Wire into daily_pull.yml so
the API stays fresh whenever the underlying data does.
"""
from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "data", "products_weekly.csv")
OUT_DIR = os.path.join(ROOT, "dashboard", "sales", "products")

VENUE_LABEL = {"stow": "Stowaway", "hg": "Harry Gatos", "mari": "Marilyna's"}
VENUE_ORDER = ["stow", "hg", "mari"]


def sydney_now_iso() -> str:
    """ISO 8601 timestamp in Australia/Sydney (fixed +10, not DST-aware
    because GitHub Actions doesn't ship tzdata for all zones and we don't
    need sub-hour precision on the freshness stamp)."""
    aest = timezone(timedelta(hours=10))
    return datetime.now(aest).replace(microsecond=0).isoformat()


def _round(v: float, ndigits: int = 2) -> float:
    return round(float(v), ndigits)


def load_rows() -> list[dict]:
    with open(SOURCE, newline="") as f:
        return list(csv.DictReader(f))


def build() -> None:
    if not os.path.exists(SOURCE):
        raise SystemExit(
            f"missing {SOURCE} — run scripts/build_products_weekly.py first"
        )

    rows = load_rows()
    print(f"read {len(rows):,} rows from {SOURCE}")

    # Group: venue -> product -> reporting_group -> list of (week_ending, sales_ex, qty)
    by_venue: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(dict))

    for r in rows:
        v = r["venue"]
        p = r["product_name"]
        rg = r["reporting_group"]
        we = r["week_ending"]
        sales = float(r["sales_ex_gst"] or 0)
        qty = float(r["qty"] or 0)
        rec = by_venue[v].setdefault(
            p,
            {
                "name": p,
                "reporting_group": rg,
                "weekly": [],
                "lifetime_qty": 0.0,
                "lifetime_revenue_ex_gst": 0.0,
                "first_week_ending": we,
                "last_week_ending": we,
            },
        )
        # If a product appears in multiple RGs across weeks (rare — happens if
        # Zak re-classifies), keep the MOST RECENT reporting_group. Weekly rows
        # come in order from the CSV so a later assignment naturally wins.
        rec["reporting_group"] = rg
        rec["weekly"].append({"we": we, "sales_ex": _round(sales), "qty": _round(qty, 3)})
        rec["lifetime_qty"] += qty
        rec["lifetime_revenue_ex_gst"] += sales
        rec["first_week_ending"] = min(rec["first_week_ending"], we)
        rec["last_week_ending"] = max(rec["last_week_ending"], we)

    os.makedirs(OUT_DIR, exist_ok=True)
    generated = sydney_now_iso()

    # Overall coverage
    coverage = {}
    for v in VENUE_ORDER:
        products = by_venue.get(v, {})
        if not products:
            continue
        all_weeks = set()
        for rec in products.values():
            for w in rec["weekly"]:
                all_weeks.add(w["we"])
        coverage[v] = {
            "label": VENUE_LABEL[v],
            "first_week_ending": min(all_weeks) if all_weeks else None,
            "last_week_ending": max(all_weeks) if all_weeks else None,
            "weeks": len(all_weeks),
            "products": len(products),
        }

    # Per-venue rollup files
    index_products = []
    for v in VENUE_ORDER:
        products = by_venue.get(v, {})
        if not products:
            continue
        cleaned = []
        for rec in products.values():
            # Sort weekly ascending by we; round lifetime totals; add avg
            rec["weekly"].sort(key=lambda w: w["we"])
            rec["lifetime_qty"] = _round(rec["lifetime_qty"], 3)
            rec["lifetime_revenue_ex_gst"] = _round(rec["lifetime_revenue_ex_gst"])
            rec["avg_price_ex_gst"] = (
                _round(rec["lifetime_revenue_ex_gst"] / rec["lifetime_qty"])
                if rec["lifetime_qty"] > 0
                else None
            )
            cleaned.append(rec)
            # Add to index
            index_products.append(
                {
                    "venue": v,
                    "name": rec["name"],
                    "reporting_group": rec["reporting_group"],
                    "first_week_ending": rec["first_week_ending"],
                    "last_week_ending": rec["last_week_ending"],
                    "lifetime_qty": rec["lifetime_qty"],
                    "lifetime_revenue_ex_gst": rec["lifetime_revenue_ex_gst"],
                }
            )
        # Sort products by lifetime revenue descending — most valuable first
        cleaned.sort(key=lambda x: x["lifetime_revenue_ex_gst"], reverse=True)
        rollup = {
            "venue": v,
            "venue_label": VENUE_LABEL[v],
            "generated_at": generated,
            "coverage": coverage[v],
            "notes": (
                "Revenue is ex-GST. week_ending is ISO date (Sunday of Mon-Sun "
                "trading week). Weekly entries are sorted ascending. "
                f"{VENUE_LABEL[v]} " + (
                    "is closed Mondays." if v == "stow" else
                    "is closed Tuesdays." if v == "hg" else
                    "has no till of its own — revenue is attributed from the Stowaway till."
                )
            ),
            "products": cleaned,
        }
        path = os.path.join(OUT_DIR, f"rollup_{v}.json")
        with open(path, "w") as f:
            json.dump(rollup, f, separators=(",", ":"))
        print(f"wrote {path} ({len(cleaned)} products, {os.path.getsize(path):,} bytes)")

    # Index — one lean record per product, all venues
    index_products.sort(key=lambda x: (-x["lifetime_revenue_ex_gst"], x["venue"], x["name"]))
    index = {
        "generated_at": generated,
        "coverage": coverage,
        "product_count": len(index_products),
        "notes": (
            "Lean product index. Use this to discover what products exist and "
            "their lifetime lifetime totals. For weekly-history detail, fetch "
            "rollup_<venue>.json where venue is one of: stow, hg, mari."
        ),
        "products": index_products,
    }
    idx_path = os.path.join(OUT_DIR, "index.json")
    with open(idx_path, "w") as f:
        json.dump(index, f, separators=(",", ":"))
    print(f"wrote {idx_path} ({len(index_products)} products, {os.path.getsize(idx_path):,} bytes)")

    # Latest — tiny freshness/coverage file
    latest = {
        "generated_at": generated,
        "coverage": coverage,
        "endpoints": {
            "index": "https://app.stowawaybar.com/sales/products/index.json",
            "stow": "https://app.stowawaybar.com/sales/products/rollup_stow.json",
            "hg": "https://app.stowawaybar.com/sales/products/rollup_hg.json",
            "mari": "https://app.stowawaybar.com/sales/products/rollup_mari.json",
            "schema_doc": "https://github.com/zakstowaway/mari-daily-reporting/blob/main/dashboard/sales/products/SCHEMA.md",
        },
        "notes": (
            "All revenue ex-GST. week_ending = Sunday of Mon-Sun trading week. "
            "Fetch index.json first to find product names, then rollup_<venue>.json "
            "for full weekly history."
        ),
    }
    latest_path = os.path.join(OUT_DIR, "latest.json")
    with open(latest_path, "w") as f:
        json.dump(latest, f, indent=2)
    print(f"wrote {latest_path}")

    print("\ndone.")


if __name__ == "__main__":
    build()
