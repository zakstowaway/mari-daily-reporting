"""
The domain core. Identity and time.

ARCHITECTURE.md decisions 1 and 2, as code. Everything above the fact layer
depends on this; this depends on nothing.

Two ideas, both load-bearing:

IDENTITY — two layers, not one
------------------------------
    Purchasable   (supplier, supplier_code)   what you BUY. The invoice gives it.
    Ingredient    canonical id                what a RECIPE says.
    map           Purchasable --many-to-one--> Ingredient

If recipes referenced supplier codes, changing supplier would break every recipe
that used the item and snap its cost history. That is exactly the hole
Lightspeed is in ("new suppliers since the food menu was updated"). With the
map, switching suppliers is one line of config: recipes keep working, and the
cost series stays continuous across the switch because both purchasables point
at the same ingredient.

TIME — everything effective-dated
---------------------------------
A cost is an OBSERVATION ON A DATE, never a current value. Ask for the cost
"as of" a day and you get what it cost then.

    cost_as_of(ing, d) = most recent observation on or before d

This exists to kill one specific bug: if recipes read a *current* cost, then
recomputing July's COGS in November prices July's dishes at November's costs,
and history silently rewrites itself. That is Average Cost Price's disease --
the thing this project exists to escape.

THE INVARIANT, and it is a test:

    Recomputing any past day gives the same answer. Forever.
"""

from __future__ import annotations

import csv
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- identity ---

def purchasable_id(supplier: str, supplier_code: str) -> str:
    """
    The natural key of a thing you buy. Given by the invoice; never invented.

    This is what Back Office's SKU field was for ("Supplier item code. Enables
    future matching without name guesswork") and why its being 3.9% populated --
    0/144 for HG liquor -- is the whole problem.
    """
    code = (supplier_code or "").strip()
    if not code:
        raise ValueError(
            f"{supplier!r} line has no supplier_code. There is no natural key, so "
            f"there is no identity. Do NOT fall back to the description -- that is "
            f"how ALEHOUSE CRISP KEG becomes the wrong $27.50 keg."
        )
    return f"{_slug(supplier)}:{code.strip().upper()}"


def ingredient_id(name: str) -> str:
    """Canonical, ours, supplier-agnostic. What recipes reference."""
    return _slug(name)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


# -------------------------------------------------------------------- time ---

@dataclass(frozen=True)
class CostObservation:
    """
    One dated, evidenced price. A FACT. Append-only: never edited, never deleted.

    Every invoice line is one of these for free -- that is the gift the invoice
    pipeline hands the rest of the system, and why throwing invoice_date away
    (as data/ingredients.json currently does) is a mistake.

    A correction is a NEW observation, not an edit. History is not rewritten.
    """
    ingredient: str
    observed_on: date
    cost_per_unit: Decimal
    unit: str
    venue: Optional[str] = None
    source_invoice: str = ""
    purchasable: str = ""

    def __post_init__(self):
        if not isinstance(self.cost_per_unit, Decimal):
            # float money is how you get 0.1 + 0.2 != 0.3 in a COGS subtraction
            raise TypeError(f"cost must be Decimal, got {type(self.cost_per_unit).__name__}")


class CostSeries:
    """
    As-of lookup over cost observations.

    Venue rule (ARCHITECTURE.md): observations carry a venue; lookup PREFERS the
    same venue and falls back to any. Stowaway and HG buy on separate accounts
    and can be quoted differently, but one venue's observation is far better
    evidence than none.
    """

    def __init__(self, observations: Iterable[CostObservation]):
        self._by: dict[tuple[str, Optional[str]], list[CostObservation]] = {}
        for o in observations:
            self._by.setdefault((o.ingredient, o.venue), []).append(o)
        for lst in self._by.values():
            lst.sort(key=lambda o: o.observed_on)

    def as_of(self, ingredient: str, on: date, venue: Optional[str] = None) -> CostObservation:
        """
        What it cost on `on`. The most recent observation on or before that day.

        Raises rather than guessing. An ingredient with no observation before the
        day being costed has no knowable cost -- inventing one (today's price,
        zero, an average) is how history starts lying. Fail toward review.
        """
        for key in ((ingredient, venue), *( ((ingredient, v) for v in self._venues(ingredient)) if venue else () )):
            hit = self._latest(key, on)
            if hit:
                return hit
        if venue is None:
            for v in self._venues(ingredient):
                hit = self._latest((ingredient, v), on)
                if hit:
                    return hit
        raise LookupError(
            f"no cost observation for {ingredient!r} on or before {on}"
            + (f" (venue {venue})" if venue else "")
            + ". Cannot cost this day. Do not substitute a current price -- that "
              "rewrites history, which is the ACP bug this design exists to avoid."
        )

    def _venues(self, ingredient: str) -> list[Optional[str]]:
        return [v for (i, v) in self._by if i == ingredient]

    def _latest(self, key, on: date) -> Optional[CostObservation]:
        lst = self._by.get(key)
        if not lst:
            return None
        i = bisect_right([o.observed_on for o in lst], on)
        return lst[i - 1] if i else None

    def __len__(self) -> int:
        return sum(len(v) for v in self._by.values())


# ------------------------------------------------------------------- load ----

def load_cost_observations(path: Path = ROOT / "data" / "cogs_list.csv",
                           purchasable_to_ingredient: Optional[dict[str, str]] = None
                           ) -> list[CostObservation]:
    """
    Read the fact log.

    NOTE: data/cogs_list.csv already IS an observation log -- every row carries
    supplier, supplier_code, cost and invoice_date. It has simply been consumed
    as a snapshot. Keep the log; derive snapshots from it.

    Until data/purchasable_map.csv exists, a purchasable maps to itself as its
    own ingredient. That is a placeholder, not the design: it means "switch
    supplier, break the recipe" is still true today. The map is Decision 1.
    """
    out = []
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        code = (r.get("supplier_code") or "").strip()
        if not code:
            continue                     # no natural key -> no identity. Skip, don't guess.
        pid = purchasable_id(r["supplier"], code)
        ing = (purchasable_to_ingredient or {}).get(pid, pid)
        out.append(CostObservation(
            ingredient=ing,
            observed_on=date.fromisoformat(r["invoice_date"]),
            cost_per_unit=Decimal(r["cost_per_unit_incl_gst"]),
            unit=(r.get("basis") or "per_unit").replace("per_", ""),
            venue=r.get("venue") or None,
            source_invoice=r.get("source_invoice", ""),
            purchasable=pid,
        ))
    return out
