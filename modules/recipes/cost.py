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
    ingredient: str          # purchasable or canonical id (empty if a sub-recipe)
    qty: Decimal
    unit: str
    desc: str = ""
    # A line can be another RECIPE instead of a bought ingredient — a sauce, a
    # batch, a dough. When set, `ingredient` is ignored and the cost comes from
    # costing that recipe and dividing by its yield. See cost_on.
    subrecipe: Optional[str] = None


@dataclass(frozen=True)
class Recipe:
    product: str
    venue: str
    lines: tuple[RecipeLine, ...]
    sell_incl_gst: Optional[Decimal] = None
    effective_from: Optional[date] = None
    entered_by: str = ""
    # Hands-on prep time to produce ONE serve of this product. Turned into a
    # dollar cost via the real kitchen wage rate (see labour.py) so GP can be
    # shown after labour, not just after food.
    prep_minutes: Optional[Decimal] = None
    # If this recipe is a batch that other recipes draw on, how much it makes:
    # yield_qty in yield_unit (e.g. 1200 g of chilli sauce). A recipe used as a
    # sub-recipe MUST declare a yield, or its per-gram cost is unknowable.
    yield_qty: Optional[Decimal] = None
    yield_unit: Optional[str] = None


class MissingCost(Exception):
    """An ingredient has no price on that day. Refuse; do not invent one."""


class CircularRecipe(Exception):
    """A sub-recipe references itself (directly or via a loop). Refuse."""


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
            prep_minutes=Decimal(str(d["prep_minutes"])) if d.get("prep_minutes") else None,
            yield_qty=Decimal(str(d["yield_qty"])) if d.get("yield_qty") else None,
            yield_unit=d.get("yield_unit") or None,
            lines=tuple(
                RecipeLine(
                    ingredient=l.get("id", ""), qty=Decimal(str(l["qty"])),
                    unit=l.get("unit", ""), desc=l.get("desc", ""),
                    subrecipe=l.get("subrecipe") or None,
                )
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
            venue: Optional[str] = None, price_mode: str = "as_of",
            recipes: Optional[list[Recipe]] = None,
            _stack: tuple[str, ...] = ()) -> Decimal:
    """
    FOOD cost per serve on `on` (ingredients only; labour is separate — see
    labour_cost / cost_breakdown).

    price_mode:
      "as_of"   (default) the price observed on or before `on`. Reproducible
                forever — recomputing July gives July's number. Use for history.
      "rolling" the trailing-30-day average as of `on`. The live working cost,
                because prices move and one odd invoice shouldn't set the menu.
                Use for today's GP, never for a historic recompute.

    Raises MissingCost rather than skipping a line. A skipped ingredient
    silently UNDERSTATES cost, which OVERSTATES GP — and errors that flatter you
    are the ones nobody investigates. That is exactly how Lightspeed reports
    Beef Cheek at 100% GP.

    Sub-recipes resolve bottom-up: a line marked `subrecipe` is costed by
    costing that recipe and dividing by its declared yield. A recipe that
    references itself, directly or through a loop, raises CircularRecipe rather
    than recursing forever.
    """
    if recipe.product in _stack:
        raise CircularRecipe(
            f"{recipe.product!r} is used (directly or via a loop) inside itself: "
            f"{' -> '.join((*_stack, recipe.product))}. A recipe cannot cost itself."
        )
    stack = (*_stack, recipe.product)
    total = Decimal("0")

    for line in recipe.lines:
        # ---- a line that is another recipe (sauce / batch / dough) ----------
        if line.subrecipe:
            sub = recipe_as_of(recipes or [], line.subrecipe, on)
            if sub is None:
                raise MissingCost(
                    f"{recipe.product!r}: sub-recipe {line.subrecipe!r} has no version "
                    f"in force on {on}. Build it before using it in another dish."
                )
            if not sub.yield_qty or not sub.yield_unit:
                raise MissingCost(
                    f"{recipe.product!r}: sub-recipe {sub.product!r} declares no yield, so "
                    f"its cost per {line.unit or 'unit'} is unknowable. Give it a yield "
                    f"(e.g. makes 1200 g) before using it as an ingredient."
                )
            if line.unit and line.unit != sub.yield_unit:
                raise MissingCost(
                    f"{recipe.product!r}: unit mismatch on sub-recipe {sub.product!r} — "
                    f"recipe wants {line.qty}{line.unit}, batch yields {sub.yield_unit}. "
                    f"Refusing to convert on a hunch."
                )
            batch_cost = cost_on(sub, costs, on, venue=venue, price_mode=price_mode,
                                 recipes=recipes, _stack=stack)
            total += (batch_cost / sub.yield_qty) * line.qty
            continue

        # ---- a bought ingredient -------------------------------------------
        try:
            if price_mode == "rolling":
                obs = costs.rolling(line.ingredient, on, venue=venue or recipe.venue)
            else:
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


def cost_breakdown(recipe: Recipe, costs: CostSeries, on: date,
                   labour: Decimal = Decimal("0"),
                   venue: Optional[str] = None, price_mode: str = "rolling",
                   recipes: Optional[list[Recipe]] = None) -> dict:
    """
    Everything the menu view needs for one product, in one place.

    Returns food cost, labour cost, and GP two ways — food-only and after prep
    labour — because a dish can look healthy on food cost and thin once the time
    someone spends prepping it is counted (Zak, 2026-07-19: "show both").

    `labour` is a dollar figure the caller computes from real prep sessions —
    each session costed at the recorder's own rate (see labour.product_labour).
    It is passed in rather than derived here so costing stays independent of who
    prepped what. Zero if the dish has no recorded prep yet.

    Defaults to rolling (live) pricing; pass price_mode="as_of" to reproduce a
    past day.
    """
    food = cost_on(recipe, costs, on, venue=venue, price_mode=price_mode, recipes=recipes)
    gp_food = gp_percent(recipe, food)
    gp_true = gp_percent(recipe, food + labour)
    return {
        "food_cost": food,
        "labour_cost": labour,
        "total_cost": food + labour,
        "gp_food": gp_food,          # margin on ingredients alone
        "gp_true": gp_true,          # margin after prep labour
        "flag": implausible(gp_food),
    }


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
