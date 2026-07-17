"""
supplier_code -> Lightspeed ProductID.

THE PROBLEM
-----------
Product names do not match between supplier invoices and Lightspeed, and
fuzzy matching them is worse than useless. The canonical example, both real,
both live at Stowaway, $27.50 apart:

    ILG 122-2867  "ALEHOUSE CRISP KEG"    -> LS 20487313 "Alehouse Summer Mid [Keg]"    $184.94
    ILG 122-2858  "ALEHOUSE PREMIUM KEG"  -> LS 20487298 "Alehouse Draught Lager [Keg]" $212.44

Both match /ALEHOUSE .* KEG/. A name matcher coin-flips. Worse, the sensible
guess is BACKWARDS: "Premium" is the Draught Lager, "Crisp" is the Summer Mid.
There is no string-similarity metric that gets this right, because the
information simply is not in the string.

THE FIX (proper)
----------------
Back Office has a SKU field, described in the skill as "Supplier item code.
Enables future matching without name guesswork." That is exactly this problem.

Measured 2026-07-16 on the real Stowaway export: SKU is populated on
84/2170 products (3.9%) -- and all 84 are kitchen items from the April
B&E/FFT bulk import, the one job that followed the convention. Of 158 liquor
products ([Keg]/[Bottle]), TWO have a SKU. None of ILG's codes appear
anywhere in the export -- not SKU, not Barcode, not ProductNo.

So the durable fix is to BACKFILL SKU. Until that is done, this module
resolves from an evidence table instead.

THE TABLE
---------
data/product_map.csv -- every row derived from a REAL invoice line matched to
a REAL export row, never from a guess. Built by scripts/build_product_map.py.

THE GUARD -- and its limits
---------------------------
Cost price is NOT a key. Back Office CostPriceIncTax is a manually-set
reference that drifts. It is only ever a CHECK on a match that name or SKU
already established. It must never itself pick the product. Two reasons,
both measured on real data 2026-07-17:

1. THE BANDS OVERLAP IN PERCENTAGE SPACE.

       Sprite Can   drift  22.2%  ($0.42  on $1.89)    <- real drift, must PASS
       Alehouse     error  14.9%  ($27.50 on $184.94)  <- real error, must FAIL

   No percentage threshold separates those. Cheap items drift hugely in
   relative terms; expensive items err hugely in absolute terms. The first
   version of this guard used max($5.00, 10%) -- and max() is OR, so either
   condition alone passed a row. On a $3 bunch of shallots the $5 floor was
   167% and silently disabled the guard entirely. Every mapping in the first
   cut was liquor, so no test caught it.

   The fix is AND, not max: a delta must be material BOTH proportionally AND
   in dollars before we call it suspect.

       |d| <= $0.02                        -> exact
       |d|/cost > 10%  AND  |d| > $5.00    -> SUSPECT   (refuse)
       otherwise                           -> stale_drift (accept)

   Checked against every measured row: Sprite 22.2%/$0.42 passes, Coke Zero
   16.3%/$0.60 passes, Kirin 0.7%/$3.13 passes, Alehouse 14.9%/$27.50 fires.

2. IT IS STRUCTURALLY BLIND TO SIMILARLY-PRICED PRODUCTS.

       Select Fresh "TOMATO BUSH POWDER 100GM"  $16.50
       Lightspeed   "Chilli Powder Korean Coarse [kg]"  $16.00

   A cost-led matcher resolved tomato powder to chilli powder on the single
   shared token "powder", and the guard passed it -- correctly, by its own
   logic, because $0.50 apart is not material. NO cost threshold catches
   this. The information is not in the price.

   Hence: cost NEVER selects. Name (exact) or SKU selects; cost checks.
   Where neither is available -- as for all 17 Harry Gatos COGS rows, which
   have no recorded Lightspeed name -- the answer is Unresolved and a human,
   NOT a clever heuristic.

Failing toward "unresolved" is correct: an unresolved line goes to human
review and costs five minutes. A line resolved to the WRONG product writes a
wrong cost against a real SKU and poisons Average Cost Price for ~30 days.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional

MAP_CSV = Path(__file__).resolve().parents[2] / "data" / "product_map.csv"

EXACT_TOL = Decimal("0.02")
# Suspect requires BOTH. See module docstring -- using max() here (i.e. OR) was
# a real bug: on cheap items the absolute floor exceeded the percentage band
# and disabled the guard.
SUSPECT_PCT = Decimal("0.10")     # material proportionally
SUSPECT_ABS = Decimal("5.00")     # AND material in dollars


def is_suspect(bo: Decimal, invoice_cost: Decimal) -> bool:
    """True only if the gap is material BOTH proportionally and in dollars."""
    d = abs(bo - invoice_cost)
    if d <= EXACT_TOL:
        return False
    pct = (d / invoice_cost) if invoice_cost else Decimal(0)
    return pct > SUSPECT_PCT and d > SUSPECT_ABS


@dataclass(frozen=True)
class Resolution:
    product_id: str
    product_name: str
    confidence: str          # exact | stale_drift | no_bo_cost
    bo_cost: Optional[Decimal]
    note: str = ""


class Unresolved(Exception):
    """Deliberate. Caller must send the line to review, NOT guess."""


class Resolver:
    def __init__(self, path: Path = MAP_CSV, venue: str = "stowaway"):
        self.venue = venue
        self._by_code: dict[tuple[str, str], dict] = {}
        if path.exists():
            for r in csv.DictReader(path.open(encoding="utf-8-sig")):
                if r.get("venue") != venue:
                    continue
                self._by_code[(r["supplier"].lower(), r["supplier_code"].strip())] = r

    def __len__(self) -> int:
        return len(self._by_code)

    def resolve(self, supplier: str, supplier_code: str,
                invoice_cost: Optional[Decimal] = None) -> Resolution:
        """
        Resolve one line. Raises Unresolved rather than guessing -- ever.

        If invoice_cost is given it is re-checked against Back Office at
        resolve time, so a mapping that was right in July still has to be
        plausible in November. Suppliers relabel codes; this catches it.
        """
        r = self._by_code.get((supplier.lower(), (supplier_code or "").strip()))
        if not r:
            raise Unresolved(
                f"{supplier} code {supplier_code!r} is not in product_map.csv "
                f"for venue {self.venue}. Add it from a real invoice -- do not guess."
            )

        bo = Decimal(r["bo_cost"]) if r.get("bo_cost") else None
        if invoice_cost is not None and bo and is_suspect(bo, invoice_cost):
            d = abs(bo - invoice_cost)
            raise Unresolved(
                f"{supplier} {supplier_code} -> {r['product_name']}: cost guard FAILED. "
                f"Back Office ${bo} vs invoice ${invoice_cost} (off by ${d}, "
                f"{100*d/invoice_cost:.1f}%). Material in BOTH dollars and percent -- "
                f"too far apart to be reference-price drift, so this is likely the "
                f"WRONG PRODUCT (cf. Alehouse Crisp/Premium, $27.50 apart). "
                f"Refusing to resolve."
            )
        return Resolution(
            product_id=r["product_id"], product_name=r["product_name"],
            confidence=r.get("confidence", ""), bo_cost=bo,
            note=f"from {r.get('source_invoice','?')} {r.get('invoice_date','')}".strip(),
        )
