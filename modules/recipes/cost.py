"""
Recipe -> cost per serve, on a given day.

This is the thing the whole project is for. Lightspeed currently reports $0.00
cost on 11 Stowaway products (4.6% of revenue, all food) because it has no
recipe for them, and 96.6% GP on Jalapeño Marg because the recipe it does have
is wrong. `daily_aggregator.py:482` copies those numbers into the app verbatim.

    cost_on(product, day) = SUM over ingredients of
                                qty x cost_as_of(ingredient, day)

AS-OF, NOT CURRENT. See ARCHITECTURE.md decision 2. Asking what a dish cost in
July must give July's answer in November, or the history rewrites itself every
time a supplier raises a price — which is Average Cost Price's exact disease and
the reason we are leaving it.

NO WASTE FACTOR (Zak, 2026-07-17). Waste is measured, not guessed:

    Xero purchases  = what you BOUGHT
    this            = what you actually USED
    the difference  = waste + theft + stock movement

Bake a guess in and you double-count waste AND destroy the only signal that
would measure it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.domain import CostSeries  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RECIPES_DIR = ROOT / "data" / "recipes"


@dataclass(frozen=True)
class RecipeLine:
    ingredient: str          # purchasable or canonical id
    qty: Decimal
    unit: str
    desc: str = ""


@dataclass(frozen=True)
class Recipe:
    product: str
    venue: str
    lines: tuple[RecipeLine, ...]
    sell_incl_gst: Optional[Decimal] = None
    effective_from: Optional[date] = None
    entered_by: str = ""


class MissingCost(Exception):
    """An ingredient has no price on that day. Refuse; do not invent one."""


def load_recipes(venue: str, path: Optional[Path] = None) -> list[Recipe]:
    p = path or (RECIPES_DIR / f"{venue}.yaml")
    if not p.exists():
        return []
    docs = yaml.safe_load(p.read_text()) or []
    out = []
    for d in docs:
        out.append(Recipe(
            product=d["product"],
            venue=venue,
            sell_incl_gst=Decimal(str(d["sell_incl_gst"])) if d.get("sell_incl_gst") else None,
            effective_from=date.fromisoformat(d["effective_from"]) if d.get("effective_from") else None,
            entered_by=d.get("entered_by", ""),
            lines=tuple(
                RecipeLine(ingredient=l["id"], qty=Decimal(str(l["qty"])),
                           unit=l.get("unit", ""), desc=l.get("desc", ""))
                for l in d.get("ingredients", [])
            ),
        ))
    return out


def recipe_as_of(recipes: list[Recipe], product: str, on: date) -> Optional[Recipe]:
    """
    The version in force on `on`. Recipes are effective-dated: editing writes a
    new version, so recomputing an old day uses the recipe that was actually
    being cooked then.
    """
    candidates = [r for r in recipes
                  if r.product == product
                  and (r.effective_from is None or r.effective_from <= on)]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.effective_from or date.min)


def cost_on(recipe: Recipe, costs: CostSeries, on: date,
            venue: Optional[str] = None) -> Decimal:
    """
    Cost per serve on `on`.

    Raises MissingCost rather than skipping an ingredient. A skipped ingredient
    silently UNDERSTATES cost, which OVERSTATES GP — and errors that flatter you
    are the ones nobody investigates. That is exactly how Lightspeed reports
    Beef Cheek at 100% GP.
    """
    total = Decimal("0")
    for line in recipe.lines:
        try:
            obs = costs.as_of(line.ingredient, on, venue=venue or recipe.venue)
        except LookupError as e:
            raise MissingCost(
                f"{recipe.product!r}: no price for {line.ingredient!r} on {on}. "
                f"Refusing to cost the dish — skipping the line would understate "
                f"cost and overstate GP. ({e})"
            ) from e

        # UNIT GUARD. Caught a 5000x error on the first real run: the cost
        # series held Foodlink squid at $57.00 PER PACK (basis 'unit', straight
        # from cogs_list.csv) while the recipe said 200 GRAMS. Multiplying blind
        # gave $11,400 per serve.
        #
        # This is the same bug as a case total landing in a per-unit field —
        # the arithmetic is perfect and the answer is nonsense. Recipes are
        # written in base units (g/ml/ea); a price quoted per pack cannot be
        # multiplied by a gram count until someone says how big the pack is.
        # Refuse; don't convert on a hunch.
        if line.unit and obs.unit and line.unit != obs.unit:
            raise MissingCost(
                f"{recipe.product!r}: unit mismatch on {line.ingredient!r} — recipe says "
                f"{line.qty}{line.unit}, price is ${obs.cost_per_unit} per '{obs.unit}'. "
                f"Multiplying these gives a number that is arithmetically perfect and "
                f"physically absurd (this exact case produced $11,400/serve). The cost "
                f"feed must publish {line.unit}-priced observations — see "
                f"modules/recipes/pipeline/build_costs.py."
            )
        total += obs.cost_per_unit * line.qty
    return total


def gp_percent(recipe: Recipe, cost: Decimal) -> Optional[Decimal]:
    """GP on the ex-GST sell price. None if we don't know what it sells for."""
    if not recipe.sell_incl_gst:
        return None
    ex = recipe.sell_incl_gst / Decimal("1.1")
    if ex <= 0:
        return None
    return (ex - cost) / ex * 100


# A GP this high means an ingredient is missing, not that the dish is a goldmine.
# Same threshold as the chef UI. Errors that flatter you are the dangerous ones.
GP_TOO_GOOD = Decimal("85")


def implausible(gp: Optional[Decimal]) -> Optional[str]:
    if gp is None:
        return None
    if gp > GP_TOO_GOOD:
        return f"GP {gp:.1f}% is too good to be true — an ingredient is probably missing"
    if gp < 0:
        return f"GP {gp:.1f}% — this dish loses money on ingredients alone"
    return None
