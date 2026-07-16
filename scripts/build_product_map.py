#!/usr/bin/env python3
"""
Build data/product_map.csv (supplier_code -> Lightspeed ProductID) from a
Back Office product export + the evidenced COGS list.

    python3 scripts/build_product_map.py \
        --export ~/Downloads/product-export_2026-07-16_223132.csv \
        --venue stowaway

Get the export: my.kounta.com -> Products -> export (cloud-down) -> Export
Products -> Show Previous Export -> Download. It is async; the button resets
to the form when the job is done. NOTE the product DB is PER-VENUE, so you
need one export per venue -- a Stowaway ProductID means nothing in HG.

Every row is derived from a real invoice line matched to a real export row.
Nothing here is fuzzy-matched. See scripts/invoices/resolve.py for why.

--emit-sku-backfill additionally writes a CSV that would populate the SKU
field, which is what Back Office's SKU is FOR ("Supplier item code. Enables
future matching without name guesswork") and which would make this whole
module unnecessary. It is NOT uploaded automatically -- that writes to the
live product database. Review it, then import via Products -> import icon.
"""

from __future__ import annotations

import argparse
import collections
import csv
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXACT_TOL = Decimal("0.02")
# SUSPECT requires BOTH material percent AND material dollars. Using max()
# (i.e. OR) was a real bug -- see scripts/invoices/resolve.py docstring.
SUSPECT_PCT = Decimal("0.10")
SUSPECT_ABS = Decimal("5.00")


def classify(bo: Decimal | None, inv: Decimal) -> tuple[str, Decimal | None]:
    if not bo:
        return "no_bo_cost", None
    d = bo - inv
    if abs(d) <= EXACT_TOL:
        return "exact", d
    pct = (abs(d) / inv) if inv else Decimal(0)
    if pct > SUSPECT_PCT and abs(d) > SUSPECT_ABS:
        return "SUSPECT", d
    return "stale_drift", d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", type=Path, required=True, help="BO product export CSV")
    ap.add_argument("--venue", default="stowaway")
    ap.add_argument("--cogs", type=Path, default=ROOT / "data" / "cogs_list.csv")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "product_map.csv")
    ap.add_argument("--emit-sku-backfill", type=Path, default=None)
    args = ap.parse_args()

    exp = list(csv.DictReader(args.export.open(encoding="utf-8-sig")))
    by_name = {r["ProductName"].strip().lower(): r for r in exp}
    cogs = list(csv.DictReader(args.cogs.open(encoding="utf-8-sig")))

    sku_pop = sum(1 for r in exp if (r.get("SKU") or "").strip())
    print(f"export: {len(exp)} products, SKU populated on {sku_pop} ({100*sku_pop/len(exp):.1f}%)")

    rows, misses, seen = [], [], set()
    for c in cogs:
        if (c.get("venue") or "") != args.venue:
            continue
        ls = (c.get("lightspeed_product") or "").strip()
        if not ls:
            continue
        r = by_name.get(ls.lower())
        if not r:
            misses.append(c)
            continue
        key = (c["supplier"], c["supplier_code"], r["ProductID"])
        if key in seen:
            continue
        seen.add(key)
        bo = Decimal(r["CostPriceIncTax"]) if r.get("CostPriceIncTax") else None
        inv = Decimal(c["cost_per_unit_incl_gst"])
        conf, d = classify(bo, inv)
        rows.append(dict(
            supplier=c["supplier"], supplier_code=c["supplier_code"],
            product_id=r["ProductID"], product_name=r["ProductName"], venue=args.venue,
            bo_cost=str(bo) if bo else "", invoice_cost=str(inv),
            delta=str(d) if d is not None else "", confidence=conf,
            source_invoice=c.get("source_invoice", ""), invoice_date=c.get("invoice_date", ""),
        ))

    print("confidence:", dict(collections.Counter(r["confidence"] for r in rows)))
    susp = [r for r in rows if r["confidence"] == "SUSPECT"]
    if susp:
        print("\nSUSPECT -- cost guard fired, do NOT ship these without checking:")
        for r in susp:
            print(f"  {r['supplier_code']:<12} {r['product_name'][:40]:<42} "
                  f"BO={r['bo_cost']:>9} inv={r['invoice_cost']:>9}")

    if misses:
        print(f"\nno name match ({len(misses)}) -- renamed, or {args.venue}-absent:")
        for c in misses:
            print(f"  {c['supplier']:<10} {c['supplier_code']:<12} {c['lightspeed_product'][:45]}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n-> {args.out.relative_to(ROOT)} ({len(rows)} rows)")

    if args.emit_sku_backfill:
        # ProductID + SKU only. Minimal columns = minimal blast radius on import.
        with args.emit_sku_backfill.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ProductID", "ProductName", "SKU"])
            for r in rows:
                if r["confidence"] != "SUSPECT":
                    w.writerow([r["product_id"], r["product_name"], r["supplier_code"]])
        print(f"-> {args.emit_sku_backfill} (REVIEW BEFORE IMPORTING -- writes to live DB)")

    return 1 if susp else 0


if __name__ == "__main__":
    raise SystemExit(main())
