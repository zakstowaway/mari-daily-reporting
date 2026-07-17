#!/usr/bin/env python3
"""
Build data/costs.csv — the cost fact table.

    python3 modules/recipes/pipeline/build_costs.py

WHAT THIS IS
------------
One row per dated, evidenced price observation, IN THE UNIT A RECIPE USES:

    ingredient, observed_on, cost_per_unit, unit, venue, source_invoice, pack

Append-only in spirit: it is derived from invoices, and an invoice is a fact.
Rebuilding it must reproduce it (CI checks this).

WHY IT EXISTS — a real 5000x bug
--------------------------------
ARCHITECTURE.md decision 2 says costs are dated observations. I built the
as-of lookup and then fed it data/cogs_list.csv directly, which quotes prices
PER PACK ($57.00 for a 5kg box of squid, basis 'unit'). A recipe says "200 g".
Multiplying those gave $11,400 per serve — arithmetically perfect, physically
absurd. Exactly the class of error the invoice validator exists to stop, and I
walked into it one layer up.

The lesson is not "add a check" (though cost_on now refuses on unit mismatch).
It is that the cost feed must publish the unit the consumer uses. A pack price
is not a gram price and no amount of care downstream fixes that.

So: pack cost / pack size -> cost per gram/ml/each, with the pack recorded so
the arithmetic is auditable. Where the pack cannot be read confidently, the
row is SKIPPED, not guessed — see build_ingredients.py for why (camembert
parsed to $364/kg on its first run).
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.domain import purchasable_id                                   # noqa: E402
from modules.recipes.pipeline.build_ingredients import (                 # noqa: E402
    KITCHEN_SUPPLIERS, out_of_bounds, parse_pack,
)

ROOT = Path(__file__).resolve().parents[3]
COGS = ROOT / "data" / "cogs_list.csv"
OUT = ROOT / "data" / "costs.csv"

FIELDS = ["ingredient", "observed_on", "cost_per_unit", "unit", "venue",
          "source_invoice", "pack", "description"]


def main() -> int:
    rows, skipped = [], []
    for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
        code = (r.get("supplier_code") or "").strip()
        if not code:
            skipped.append((r["supplier"], r["invoice_description"], "no supplier_code — no identity"))
            continue

        desc = r["invoice_description"].strip()
        pack_cost = Decimal(r["cost_per_unit_incl_gst"])

        # Liquor is already priced in the unit a recipe uses: a bottle IS the
        # unit, a keg IS the unit. Only kitchen goods need pack -> gram.
        if r["supplier"] not in KITCHEN_SUPPLIERS:
            basis = (r.get("basis") or "per_unit").replace("per_", "")
            unit = {"bottle": "bottle", "keg": "keg", "can": "can"}.get(basis, "ea")
            rows.append(dict(
                ingredient=purchasable_id(r["supplier"], code),
                observed_on=r["invoice_date"], cost_per_unit=str(pack_cost), unit=unit,
                venue=r.get("venue") or "", source_invoice=r.get("source_invoice", ""),
                pack="1", description=desc,
            ))
            continue

        qty, unit, how = parse_pack(desc)
        if not qty or not unit:
            skipped.append((r["supplier"], desc, f"pack unreadable ({how})"))
            continue

        per = (pack_cost / qty).quantize(Decimal("0.000001"))
        bad = out_of_bounds(per, unit)
        if bad:
            # Arithmetically fine, physically absurd. Do not publish it.
            skipped.append((r["supplier"], desc, bad))
            continue

        rows.append(dict(
            ingredient=purchasable_id(r["supplier"], code),
            observed_on=r["invoice_date"], cost_per_unit=str(per), unit=unit,
            venue=r.get("venue") or "", source_invoice=r.get("source_invoice", ""),
            pack=how, description=desc,
        ))

    rows.sort(key=lambda x: (x["ingredient"], x["observed_on"]))
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} cost observations -> {OUT.relative_to(ROOT)}")
    print(f"  skipped {len(skipped)} (not guessed — see below)")
    for s, d, why in skipped[:8]:
        print(f"    {s:<13} {d[:34]:<36} {why[:60]}")
    print("\nsample:")
    for r in rows[:6]:
        print(f"  {r['ingredient']:<22} {r['observed_on']}  ${r['cost_per_unit']:>10}/{r['unit']:<6} "
              f"(pack {r['pack']}, inv {r['source_invoice']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
