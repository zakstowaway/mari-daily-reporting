#!/usr/bin/env python3
"""
Build the cross-supplier price comparison the app renders at /pricing.

    python3 modules/invoices/build_price_compare.py

Reads data/cogs_list.csv (every ingredient line the invoice pipeline has ever
validated), reduces each row to a canonical $/kg | $/L | $/each, groups rows
that are the SAME ingredient (price_compare.canonical_key), and for each
ingredient records the latest cost PER SUPPLIER plus its movement since the
previous invoice. Writes dashboard/pricing/compare.json.

The value is the multi-supplier groups: "chicken breast — B&E $12.40/kg vs
Foodlink $11.90/kg", cheapest flagged, spread shown. Single-supplier items still
appear as a price list with movement, so a creeping cost gets noticed.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.invoices.pack_size import parse_pack                # noqa: E402
from modules.invoices.price_compare import (                     # noqa: E402
    canonical_key, display_name, _load_aliases,
)

ROOT = Path(__file__).resolve().parents[2]
COGS = ROOT / "data" / "cogs_list.csv"
OUT = ROOT / "dashboard" / "pricing" / "compare.json"


def _dec(s) -> Decimal | None:
    try:
        return Decimal(str(s))
    except (InvalidOperation, TypeError):
        return None


def _base_cost(row: dict) -> tuple[Decimal | None, str]:
    """($/base, base_unit) for one cogs row. Weight-priced rows are already $/kg."""
    price = _dec(row.get("cost_per_unit_incl_gst"))
    if price is None:
        return None, "ea"
    weight_priced = (row.get("basis") == "per_kg")
    pq, pu = parse_pack(row.get("invoice_description", ""), row.get("note") or None,
                        is_weight_priced=weight_priced)
    base = (price / pq) if pq and pq > 0 else price
    return base, pu


def build() -> dict:
    if not COGS.exists():
        return {"generated": date.today().isoformat(), "ingredients": []}
    aliases = _load_aliases()
    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))

    # group: (key, unit) -> ingredient; within it, supplier -> list of (date, cost, desc)
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        desc = (r.get("invoice_description") or "").strip()
        supplier = (r.get("supplier") or "").strip()
        if not desc or not supplier:
            continue
        base, unit = _base_cost(r)
        if base is None or base <= 0:
            continue
        key = canonical_key(desc, aliases)
        if not key:
            continue
        g = groups.setdefault((key, unit), {"key": key, "unit": unit,
                                            "names": {}, "suppliers": {}})
        # remember candidate display names (shortest identity wins — most generic)
        g["names"][display_name(desc)] = g["names"].get(display_name(desc), 0) + 1
        g["suppliers"].setdefault(supplier, []).append(
            (r.get("invoice_date") or "", float(base), desc))

    ingredients = []
    for (key, unit), g in groups.items():
        sup_rows = []
        for supplier, obs in g["suppliers"].items():
            obs.sort(key=lambda o: o[0])                 # by date asc
            d, cost, desc = obs[-1]                       # latest
            prev = next((o for o in reversed(obs[:-1]) if o[1] != cost), None)
            change = round((cost - prev[1]) / prev[1] * 100, 1) if prev and prev[1] else None
            sup_rows.append({
                "supplier": supplier, "cost": round(cost, 4), "date": d,
                "desc": desc, "change_pct": change, "n": len(obs),
            })
        sup_rows.sort(key=lambda s: s["cost"])
        cheapest = sup_rows[0]["supplier"]
        lo, hi = sup_rows[0]["cost"], sup_rows[-1]["cost"]
        spread = round((hi - lo) / lo * 100, 1) if lo else 0.0
        multi = len(sup_rows) > 1
        # An implausible gap (>150%) between two "same" items is almost always a
        # pack-size mismatch — one priced per tray/dozen, the other per kg — not a
        # real saving. Flag it so the reviewer verifies (and can add an alias)
        # rather than being told to switch supplier on a phantom number.
        suspect = multi and spread > 150
        # display: the most-seen shortest name
        name = min(sorted(g["names"], key=lambda n: (-g["names"][n], len(n))),
                   key=lambda n: (len(n), -g["names"][n]))
        ingredients.append({
            "key": key, "name": name, "unit": unit,
            "suppliers": sup_rows, "cheapest": cheapest,
            "min": round(lo, 4), "max": round(hi, 4), "spread_pct": spread,
            "multi": multi, "suspect": suspect,
        })

    # real comparisons first (biggest spread = biggest saving), then the
    # verify-pack ones, then single-supplier price list A–Z
    ingredients.sort(key=lambda i: (
        not (i["multi"] and not i["suspect"]),
        -i["spread_pct"] if (i["multi"] and not i["suspect"]) else 0,
        not i["suspect"], i["name"].lower()))
    return {"generated": date.today().isoformat(),
            "count": len(ingredients),
            "compared": sum(1 for i in ingredients if i["multi"]),
            "ingredients": ingredients}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    OUT.write_text(json.dumps(data, indent=2))
    print(f"compare.json: {data['count']} ingredients, "
          f"{data['compared']} compared across >1 supplier -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
