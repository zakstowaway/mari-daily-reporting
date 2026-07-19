#!/usr/bin/env python3
"""
Publish the small read-only feeds the recipe builder needs in the browser.

    python3 modules/recipes/pipeline/build_recipe_feeds.py

Three files, all derived, none hand-maintained:

  data/labour_rate.json   the team-average $/min for the LIVE "GP after labour"
                          estimate. A mean — no individual's wage is in it. The
                          real per-person costing stays server-side.

  data/recipes_index.json existing recipes that can be used as sub-recipes:
                          product, venue, yield, and current (rolling) cost per
                          yield-unit. This is how the builder offers "add a
                          sauce/batch" without shipping every recipe's guts.

  data/employees.json     Deputy id -> name, so the Team page can link each
                          login to a real employee (whose rate costs their prep).
                          Names only; no pay.

Generated at build time (build_site.py runs this), never committed — same class
as data/ingredients.json.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core.domain import CostSeries, load_cost_observations       # noqa: E402
from modules.recipes.cost import cost_on, load_recipes           # noqa: E402
from modules.recipes.labour import venue_estimate_rate_per_minute  # noqa: E402

DATA = ROOT / "data"
VENUES = ["stowaway", "harry_gatos", "marilynas"]


def _dec(x) -> str:
    return format(x, "f")


def labour_rate() -> dict:
    out = {"generated_at": date.today().isoformat(), "note": "team-average estimate for live display only; real cost is per-recorder server-side", "venues": {}}
    for v in VENUES:
        r = venue_estimate_rate_per_minute(v)
        out["venues"][v] = {"rate_per_minute": _dec(r)} if r is not None else None
    # a default, so the builder always has something to estimate with
    default = venue_estimate_rate_per_minute(None)
    out["default_rate_per_minute"] = _dec(default) if default is not None else None
    return out


def recipes_index() -> dict:
    try:
        costs = CostSeries(load_cost_observations())
    except FileNotFoundError:
        costs = CostSeries([])
    today = date.today()
    items = []
    for v in VENUES:
        recipes = load_recipes(v)
        # latest version per product
        latest: dict[str, object] = {}
        for r in recipes:
            cur = latest.get(r.product)
            if cur is None or (r.effective_from or date.min) >= (cur.effective_from or date.min):
                latest[r.product] = r
        for r in latest.values():
            entry = {
                "product": r.product,
                "venue": v,
                "yield_qty": _dec(r.yield_qty) if r.yield_qty else None,
                "yield_unit": r.yield_unit,
                "usable_as_subrecipe": bool(r.yield_qty and r.yield_unit),
                "cost": None,
                "cost_per_yield_unit": None,
            }
            try:
                c = cost_on(r, costs, today, price_mode="rolling", recipes=recipes)
                entry["cost"] = _dec(c.quantize(Decimal("0.0001")))
                if r.yield_qty:
                    entry["cost_per_yield_unit"] = _dec((c / r.yield_qty).quantize(Decimal("0.000001")))
            except Exception:
                pass   # a recipe we can't fully cost yet still lists for selection
            items.append(entry)
    return {"generated_at": today.isoformat(), "recipes": items}


def employees() -> dict:
    p = DATA / "employee_map.json"
    m = json.loads(p.read_text()) if p.exists() else {}
    people = [{"id": str(k), "name": v} for k, v in m.items()]
    people.sort(key=lambda e: e["name"].lower())
    return {"generated_at": date.today().isoformat(), "employees": people}


def main() -> int:
    (DATA / "labour_rate.json").write_text(json.dumps(labour_rate(), indent=2))
    idx = recipes_index()
    (DATA / "recipes_index.json").write_text(json.dumps(idx, indent=2))
    (DATA / "employees.json").write_text(json.dumps(employees(), indent=2))
    print(f"labour_rate.json, recipes_index.json ({len(idx['recipes'])} recipes), employees.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
