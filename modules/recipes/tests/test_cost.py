"""
Recipe costing tests.

Built on the real Southern Squid case: $79 revenue, 3 sold, and Lightspeed
reports $0.00 cost for it today.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.domain import CostObservation, CostSeries  # noqa: E402
from modules.recipes.cost import (  # noqa: E402
    MissingCost, Recipe, RecipeLine, cost_on, gp_percent, implausible,
    load_recipes, recipe_as_of,
)

SQUID = "foodlink:102689"


@pytest.fixture
def costs():
    # real: Foodlink SI4467596, $57.00 / 5kg = $0.0114/g
    return CostSeries([
        CostObservation(SQUID, date(2026, 5, 1),  Decimal("0.0100"), "g", "stowaway", "OLD"),
        CostObservation(SQUID, date(2026, 7, 16), Decimal("0.0114"), "g", "stowaway", "SI4467596"),
        CostObservation("oil", date(2026, 7, 1),  Decimal("0.0040"), "ml", "stowaway", "X"),
    ])


@pytest.fixture
def squid():
    return Recipe(
        product="Southern Squid", venue="stowaway",
        sell_incl_gst=Decimal("26.33"),
        lines=(RecipeLine(SQUID, Decimal("200"), "g", "SQUID PINEAPPLE CUT IMP U5 5KG"),),
    )


def test_cost_matches_the_hand_calculation(squid, costs):
    # 200g x $0.0114 = $2.28 — same number the chef UI showed
    assert cost_on(squid, costs, date(2026, 7, 20)) == Decimal("2.28")


def test_cost_uses_the_price_on_that_day_not_todays(squid, costs):
    """
    THE INVARIANT. July's dish costs July's price, forever — even though a later
    (dearer) observation exists.
    """
    assert cost_on(squid, costs, date(2026, 7, 15)) == Decimal("2.00")   # 200 x 0.0100
    assert cost_on(squid, costs, date(2026, 7, 20)) == Decimal("2.28")   # 200 x 0.0114


def test_missing_price_refuses_rather_than_skipping(costs):
    """
    Skipping an unpriced line UNDERSTATES cost and OVERSTATES GP. That is
    precisely how Lightspeed reports Beef Cheek at 100% GP. Refuse instead.
    """
    r = Recipe("Mystery", "stowaway", lines=(RecipeLine("unicorn-tears", Decimal("1"), "g"),))
    with pytest.raises(MissingCost, match="understate cost"):
        cost_on(r, costs, date(2026, 7, 20))


def test_price_before_any_observation_refuses(squid, costs):
    with pytest.raises(MissingCost):
        cost_on(squid, costs, date(2026, 1, 1))


def test_gp_is_calculated_ex_gst(squid, costs):
    c = cost_on(squid, costs, date(2026, 7, 20))          # 2.28
    gp = gp_percent(squid, c)                              # sell 26.33 incl -> 23.94 ex
    assert gp is not None
    assert Decimal("90") < gp < Decimal("91")              # 90.5%


def test_too_high_gp_is_flagged_not_celebrated(squid, costs):
    """
    Squid alone is 90.5% — no batter, no oil, no aioli, no lemon. That is a
    warning, not a win. Errors that flatter you are the ones nobody checks.
    """
    gp = gp_percent(squid, cost_on(squid, costs, date(2026, 7, 20)))
    assert "too good to be true" in implausible(gp)


def test_a_complete_recipe_lands_in_a_believable_range(costs):
    full = Recipe(
        product="Southern Squid", venue="stowaway", sell_incl_gst=Decimal("26.33"),
        lines=(RecipeLine(SQUID, Decimal("200"), "g"),
               RecipeLine("oil", Decimal("800"), "ml")),      # $3.20 of oil
    )
    c = cost_on(full, costs, date(2026, 7, 20))               # 2.28 + 3.20 = 5.48
    gp = gp_percent(full, c)
    assert implausible(gp) is None, f"GP {gp:.1f}% should be plausible"
    assert Decimal("70") < gp < Decimal("80")


def test_negative_gp_is_flagged():
    r = Recipe("Loss Leader", "stowaway", sell_incl_gst=Decimal("5.00"),
               lines=(RecipeLine(SQUID, Decimal("1000"), "g"),))
    s = CostSeries([CostObservation(SQUID, date(2026, 7, 1), Decimal("0.0114"), "g", "stowaway", "X")])
    gp = gp_percent(r, cost_on(r, s, date(2026, 7, 20)))
    assert "loses money" in implausible(gp)


def test_no_sell_price_means_no_gp(costs):
    r = Recipe("Staff Feed", "stowaway", lines=(RecipeLine(SQUID, Decimal("100"), "g"),))
    assert gp_percent(r, cost_on(r, costs, date(2026, 7, 20))) is None


# ---- effective-dated recipes ----------------------------------------------

def test_recipe_as_of_picks_the_version_in_force():
    """
    Change a recipe in September and July's COGS must still use July's recipe.
    Same reasoning as costs: an edit is a new version, not an overwrite.
    """
    v1 = Recipe("Squid", "stowaway", effective_from=date(2026, 1, 1),
                lines=(RecipeLine(SQUID, Decimal("200"), "g"),))
    v2 = Recipe("Squid", "stowaway", effective_from=date(2026, 9, 1),
                lines=(RecipeLine(SQUID, Decimal("250"), "g"),))
    rs = [v1, v2]
    assert recipe_as_of(rs, "Squid", date(2026, 7, 20)).lines[0].qty == Decimal("200")
    assert recipe_as_of(rs, "Squid", date(2026, 9, 5)).lines[0].qty == Decimal("250")
    assert recipe_as_of(rs, "Squid", date(2025, 1, 1)) is None      # before it existed
    assert recipe_as_of(rs, "Nope", date(2026, 7, 20)) is None


def test_load_recipes_reads_what_the_chef_ui_writes(tmp_path):
    """The UI's YAML must parse. If this breaks, saved recipes are unreadable."""
    p = tmp_path / "stowaway.yaml"
    p.write_text("""
- product: "Southern Squid"
  sell_incl_gst: 26.33
  effective_from: "2026-07-17"
  entered_by: "Sam Taylor"
  ingredients:
    - id: foodlink:102689
      desc: "SQUID PINEAPPLE CUT IMP U5 5KG"
      qty: 200
      unit: g
      unit_cost_incl: 0.011400
""")
    rs = load_recipes("stowaway", path=p)
    assert len(rs) == 1
    r = rs[0]
    assert r.product == "Southern Squid"
    assert r.entered_by == "Sam Taylor"          # the audit trail
    assert r.effective_from == date(2026, 7, 17)
    assert r.lines[0].qty == Decimal("200")


def test_missing_recipe_file_is_empty_not_an_error():
    assert load_recipes("nonexistent_venue") == []


# ---- the 5000x bug, pinned -------------------------------------------------

def test_unit_mismatch_refuses_rather_than_multiplying():
    """
    THE ONE THAT SHIPPED, briefly, on 2026-07-17.

    The cost series held Foodlink squid at $57.00 PER PACK (read straight from
    cogs_list.csv, basis 'unit') while the recipe said 200 GRAMS. cost_on
    multiplied them and returned $11,400 per serve. Arithmetically perfect,
    physically absurd — the same class of error as a case total in a per-unit
    field, one layer up.
    """
    pack_priced = CostSeries([
        CostObservation(SQUID, date(2026, 7, 16), Decimal("57.00"), "unit", "stowaway", "SI4467596"),
    ])
    r = Recipe("Southern Squid", "stowaway",
               lines=(RecipeLine(SQUID, Decimal("200"), "g"),))
    with pytest.raises(MissingCost, match="unit mismatch"):
        cost_on(r, pack_priced, date(2026, 7, 20))


def test_the_real_feed_prices_in_recipe_units():
    """
    data/costs.csv must publish g/ml/ea — the unit a recipe uses. If it ever
    goes back to pack prices, the mismatch guard fires on every dish and
    nothing can be costed.
    """
    from core.domain import load_cost_observations
    obs = load_cost_observations()
    assert obs, "no cost observations — run modules/recipes/pipeline/build_costs.py"
    units = {o.unit for o in obs}
    assert units <= {"g", "ml", "ea", "bunch", "tray", "punnet", "doz", "bottle", "keg", "can"}, \
        f"unexpected units in the cost feed: {units}"
    assert "unit" not in units, "'unit' means a pack price leaked into the feed"


def test_bare_kg_is_priced_per_kilo():
    """
    'ONION BROWN KG' is not a missing pack size — it is how produce is sold.
    Missing this skipped most of Select Fresh, i.e. most of what a kitchen cooks.
    """
    from modules.recipes.pipeline.build_ingredients import parse_pack
    qty, unit, how = parse_pack("ONION BROWN KG")
    assert (qty, unit) == (Decimal(1000), "g")
    assert how == "per kg"
    # and it must not fire on a real pack size
    assert parse_pack("CHICKEN THIGH 5KG BAG")[0] == Decimal(5000)
