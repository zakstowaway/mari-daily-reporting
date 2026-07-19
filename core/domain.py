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
from datetime import date, timedelta
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
    # How much was BOUGHT on this invoice line, in `unit`. Optional: the invoice
    # pipeline does not capture it yet, so it is None today and the rolling
    # average weights every observation equally (a plain mean). The day the
    # pipeline records quantities, this turns the same average volume-weighted
    # with no code change — a bulk buy will count more than a top-up, which is
    # what you actually paid. See CostSeries.rolling.
    qty: Optional[Decimal] = None

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

    def rolling(self, ingredient: str, on: date, window_days: int = 30,
                venue: Optional[str] = None) -> CostObservation:
        """
        The CURRENT working cost: a trailing `window_days` average as of `on`.

        Prices move (Zak, 2026-07-19: "prices move over time"). Pricing today's
        menu off a single latest invoice is noisy — one odd delivery sets your
        cost. So the live cost is the average of what you paid over the last
        month, weighted by how much you bought (volume-weighted) when the
        quantity is known, and a plain mean when it is not (which is the case
        today — see CostObservation.qty).

        This is the CURRENT view only. Historic reproducibility still runs
        through as_of: recomputing July's COGS must give July's answer forever,
        and an average that changes as new invoices land would rewrite it. So
        cost_on uses as_of for a past day and rolling for the live number.

        Degrades safely:
          * one observation in the window  -> that price
          * none in the window (but older exists) -> most recent (as_of)
          * mixed units in the window -> most recent, not a meaningless average
        """
        key = self._pick_key(ingredient, on, venue)
        if key is None:
            # Reuse as_of purely to raise the same, well-explained LookupError.
            return self.as_of(ingredient, on, venue=venue)

        lst = self._by[key]
        start = on - timedelta(days=window_days)
        window = [o for o in lst if start < o.observed_on <= on]
        if not window:
            return self.as_of(ingredient, on, venue=venue)   # fall back to latest

        units = {o.unit for o in window}
        if len(units) > 1:
            # Averaging g-prices with pack-prices is the $11,400/serve bug in a
            # different hat. Refuse the average; use the latest single fact.
            return self._latest(key, on)

        # Volume-weighted when every line knows its quantity; else equal weight.
        if all(o.qty is not None and o.qty > 0 for o in window):
            weight = {id(o): o.qty for o in window}
        else:
            weight = {id(o): Decimal("1") for o in window}
        wsum = sum(weight.values())
        avg = sum(o.cost_per_unit * weight[id(o)] for o in window) / wsum

        latest = max(window, key=lambda o: o.observed_on)
        return CostObservation(
            ingredient=ingredient,
            observed_on=latest.observed_on,
            cost_per_unit=avg,
            unit=window[0].unit,
            venue=key[1],
            source_invoice=f"avg of {len(window)} obs, {window_days}d",
        )

    def _pick_key(self, ingredient: str, on: date,
                  venue: Optional[str]) -> Optional[tuple[str, Optional[str]]]:
        """The venue bucket as_of would resolve to — same preference rule."""
        order: list[tuple[str, Optional[str]]] = []
        if venue is not None:
            order.append((ingredient, venue))
        for v in self._venues(ingredient):
            if (ingredient, v) not in order:
                order.append((ingredient, v))
        for key in order:
            if self._latest(key, on):
                return key
        return None

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

def load_ingredient_map(path: Path = ROOT / "data" / "ingredient_map.csv"
                        ) -> dict[str, str]:
    """
    purchasable_id -> canonical ingredient_id, ONLY where a human confirmed it.

    This is Decision 1's map (ARCHITECTURE.md): it lets "Select Fresh ONIBK" and
    "B&E onion" be declared the SAME ingredient, so switching supplier does not
    break a recipe or snap its cost history.

    Empty today, and correctly so: the current 55 observations have no
    cross-supplier duplicate, so there is nothing yet to merge. A purchasable
    with no row here maps to itself (see load_cost_observations). The file
    exists so that the day a second onion supplier appears, confirming they are
    one ingredient is a one-line edit — reviewed in a diff, attributed via
    confirmed_by — not a code change.
    """
    if not path.exists():
        return {}
    out = {}
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        pid = (r.get("purchasable_id") or "").strip()
        ing = (r.get("ingredient_id") or "").strip()
        if pid and ing:
            out[pid] = ing
    return out


def load_cost_observations(path: Path = ROOT / "data" / "costs.csv",
                           purchasable_to_ingredient: Optional[dict[str, str]] = None
                           ) -> list[CostObservation]:
    """
    Read the cost fact table: data/costs.csv.

    Prices are IN THE UNIT A RECIPE USES (per g / ml / ea / bottle / keg),
    because that is the consumer. Built by
    modules/recipes/pipeline/build_costs.py, which converts pack prices and
    REFUSES rather than guessing when a pack can't be read.

    THIS USED TO READ data/cogs_list.csv DIRECTLY AND IT WAS WRONG. That file
    quotes per PACK ($57.00 for a 5kg box of squid, basis 'unit'). A recipe says
    "200 g". Multiplying gave $11,400 per serve -- arithmetically perfect,
    physically absurd, the same class of error the invoice validator exists to
    stop. A feed must publish the unit its consumer uses; no amount of care
    downstream fixes a pack price masquerading as a gram price.

    Until data/purchasable_map.csv exists, a purchasable maps to itself as its
    own ingredient. That is a placeholder, not the design: it means "switch
    supplier, break the recipe" is still true today. The map is Decision 1.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing — run: python3 modules/recipes/pipeline/build_costs.py"
        )
    # Default to the confirmed map on disk; caller may override for tests.
    mapping = purchasable_to_ingredient if purchasable_to_ingredient is not None \
        else load_ingredient_map()
    out = []
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        pid = r["ingredient"]
        ing = mapping.get(pid, pid)     # unmapped purchasable = its own ingredient
        out.append(CostObservation(
            ingredient=ing,
            observed_on=date.fromisoformat(r["observed_on"]),
            cost_per_unit=Decimal(r["cost_per_unit"]),
            unit=r["unit"],
            venue=r.get("venue") or None,
            source_invoice=r.get("source_invoice", ""),
            purchasable=pid,
        ))
    return out
