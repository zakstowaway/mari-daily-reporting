#!/usr/bin/env python3
"""
Aggregate validated invoices -> data/cogs_list.csv (the recipe cost feed).

    python3 modules/invoices/build_cogs_list.py

THE MISSING LINK. run.py turns one PDF into one validated invoice JSON in
data/invoices/. Nothing rolled those into cogs_list.csv — so the list was
hand-built during the first sweep, and every new invoice meant manual work
(exactly what Zak watched happen). This closes the loop: a validated invoice in
data/invoices/ becomes rows in the recipe system, no hands.

MERGE, NOT REGENERATE. cogs_list.csv already holds hand-entered rows from the
sweep that have no invoice JSON behind them. This ADDS validated invoice lines
that aren't already present (keyed by invoice_ref + supplier_code/description)
and leaves everything else untouched. Idempotent: run twice, same result.

Only STOCK lines become cost rows. Freight, fuel levies, WET adjustments and
'waiting on stock' lines are excluded — they are not ingredients.

The `supplier` column must be the SHORT name the recipe pipeline recognises
(build_ingredients.KITCHEN_SUPPLIERS), not the long legal name on the invoice —
so a per-supplier alias lives here. A supplier with no alias falls back to its
display name and simply won't be treated as a kitchen good until added.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INVOICES = ROOT / "data" / "invoices"          # PASS invoices from run.py
COGS = ROOT / "data" / "cogs_list.csv"

FIELDS = ["supplier", "supplier_code", "invoice_description", "lightspeed_product",
          "cost_per_unit_incl_gst", "basis", "pack_size",
          "pack_qty", "pack_unit", "cost_per_base_unit",   # canonical $/kg, $/L, $/each
          "venue", "source_invoice", "invoice_date", "in_bounds", "note"]

# supplier_key (suppliers.yaml) -> the short name cogs_list / the recipe
# pipeline uses. Kitchen names here MUST match build_ingredients.KITCHEN_SUPPLIERS.
SUPPLIER_ALIAS = {
    "fresh_fruit_team": "Fresh Fruit Team", "select_fresh": "Select Fresh",
    "be_foods": "B&E", "foodlink": "Foodlink", "gulli": "Gulli",
    "andrews_meat": "Andrews Meat", "aquarius": "Aquarius", "mj_chickens": "M&J Chickens",
    "cookers": "Cookers", "torino": "Torino", "captains_of_trade": "Captains of Trade",
    "ilg": "ILG", "ilg_distribution_coop": "ILG", "paramount": "Paramount",
    "lion": "Lion", "viticult": "Viticult", "nelson_wine": "Nelson",
    "combined_wines": "Combined Wines", "bacchus": "Bacchus", "grifter": "Grifter",
    "philter": "Philter", "young_rashleigh": "Young & Rashleigh",
    "mountain_culture": "Mountain Culture", "four_pines": "4 Pines",
}


def _key(source_invoice: str, code: str, desc: str) -> tuple[str, str]:
    """Identity of a cost row: one line per (invoice, product)."""
    return (source_invoice.strip(), (code or desc).strip().upper())


def _load_existing() -> tuple[list[dict], set[tuple[str, str]]]:
    if not COGS.exists():
        return [], set()
    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
    seen = {_key(r["source_invoice"], r.get("supplier_code", ""), r["invoice_description"])
            for r in rows}
    return rows, seen


def _rows_from_invoice(payload: dict) -> list[dict]:
    inv = payload["invoice"]
    supplier = SUPPLIER_ALIAS.get(inv.get("supplier_key", ""),
                                  inv.get("supplier_name_raw", inv.get("supplier_key", "")))
    venue = (inv.get("venue") or "unknown")
    ref = inv.get("invoice_ref", "")
    d = inv.get("invoice_date", "")
    out = []
    for ln in inv.get("lines", []):
        if ln.get("line_class") != "stock":        # only real ingredients
            continue
        code = ln.get("supplier_code") or ""
        desc = (ln.get("description") or "").strip()
        price = ln.get("unit_price_incl")
        if price is None:                           # derive per-unit if needed
            tot, qty = ln.get("line_total_incl"), ln.get("qty")
            if tot and qty and str(qty) not in ("0", "0.0"):
                from decimal import Decimal
                price = str((Decimal(str(tot)) / Decimal(str(qty))).quantize(Decimal("0.0001")))
        if price is None:
            continue
        note = "; ".join(ln.get("notes", []) or []) or (ln.get("raw_uom") or "")
        # canonical cost per base unit ($/kg, $/L, $/each) — comparable across suppliers
        from decimal import Decimal, InvalidOperation
        pq, pu = ln.get("pack_qty"), ln.get("pack_unit") or "ea"
        try:
            base = ((Decimal(str(price)) / Decimal(str(pq))).quantize(Decimal("0.0001"))
                    if pq and Decimal(str(pq)) > 0 else Decimal(str(price)))
        except (InvalidOperation, TypeError):
            base = price
        out.append({
            "supplier": supplier,
            "supplier_code": code,
            "invoice_description": desc,
            "lightspeed_product": ln.get("lightspeed_product_name") or "",
            "cost_per_unit_incl_gst": str(price),
            "basis": ln.get("cost_basis") or "per_unit",
            "pack_size": str(ln.get("pack_size") or 1),
            "pack_qty": str(pq or 1),
            "pack_unit": pu,
            "cost_per_base_unit": str(base),
            "venue": venue,
            "source_invoice": ref,
            "invoice_date": d,
            "in_bounds": "yes",                     # only PASS invoices reach here
            "note": note,
        })
    return out


def main() -> int:
    rows, seen = _load_existing()
    added, invoices = 0, 0
    for p in sorted(INVOICES.glob("*.json")) if INVOICES.exists() else []:
        try:
            payload = json.loads(p.read_text())
        except Exception as e:
            print(f"  skip {p.name}: {e}")
            continue
        invoices += 1
        for row in _rows_from_invoice(payload):
            k = _key(row["source_invoice"], row["supplier_code"], row["invoice_description"])
            if k in seen:
                continue
            seen.add(k)
            rows.append(row)
            added += 1

    rows.sort(key=lambda r: (r["invoice_date"], r["supplier"], r["supplier_code"], r["invoice_description"]))
    with COGS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"{added} new rows from {invoices} validated invoice(s) -> "
          f"{COGS.relative_to(ROOT)} ({len(rows)} rows total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
