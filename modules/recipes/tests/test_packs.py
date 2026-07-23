"""
Pack resolution — turning a supplier line into a cost in a chef's unit, using
the invoice's structured basis + note, not just the free-text description.

Every case here is a REAL line from data/cogs_list.csv that used to be either
uncostable or wrong.
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.recipes.pipeline.build_ingredients import resolve_pack  # noqa: E402


def test_per_kg_is_costed_per_gram_even_with_no_weight_in_the_name():
    # "Cauliflower Florets" has no KG in the text — the invoice basis does.
    qty, unit, per, how, bad = resolve_pack("Cauliflower Florets", "7.90", basis="per_kg")
    assert (unit, per, bad) == ("g", Decimal("0.007900"), None)
    assert qty == Decimal("1000")


def test_each_from_the_note():
    qty, unit, per, how, bad = resolve_pack("Celeriac", "10.80", basis="per_unit", note="Each")
    assert (unit, per, bad) == ("ea", Decimal("10.80"), None)


def test_punnet_from_the_note():
    _, unit, per, _, bad = resolve_pack("Tomatoes Cherry", "1.76", basis="per_unit", note="Punnet")
    assert (unit, per, bad) == ("punnet", Decimal("1.76"), None)


def test_box_from_the_note():
    _, unit, per, _, bad = resolve_pack("Broccolini", "33.60", basis="per_unit", note="Box")
    assert (unit, per, bad) == ("box", Decimal("33.60"), None)


def test_carton_note_rescues_a_piece_price():
    # $45.60 is a BOX OF 12 x 125g wheels, not one 125g wheel. Without the note
    # this parses to $0.365/g and is (correctly) refused as too dear.
    qty, unit, per, how, bad = resolve_pack(
        "CHEESE CAMEMBERT 125GM Rosenberg", "45.60", basis="per_unit", note="UOM CTN-12")
    assert unit == "g"
    assert qty == Decimal("1500")            # 12 x 125g
    assert per == Decimal("0.030400")
    assert bad is None                       # now in bounds


def test_multipack_in_the_description_is_not_double_counted_by_a_ctn_note():
    # "6X500GM" already IS the pack; a stray CTN note must not multiply again.
    qty, unit, per, how, bad = resolve_pack(
        "CORN CHIPS ROSITA TRI 6X500GM", "47.30", basis="per_unit", note="UOM CTN-6")
    assert qty == Decimal("3000")            # 6 x 500g, NOT x6 again
    assert bad is None


def test_liquor_bottle_is_priced_per_bottle():
    _, unit, per, _, bad = resolve_pack("Aperol", "29.08", basis="per_bottle")
    assert (unit, per, bad) == ("bottle", Decimal("29.08"), None)


def test_the_piece_price_trap_still_refuses_when_there_is_no_carton_note():
    # Same camembert, but no note telling us it's a carton -> too dear -> review.
    _, unit, per, _, bad = resolve_pack("CHEESE CAMEMBERT 125GM Rosenberg", "45.60", basis="per_unit")
    assert unit == "g" and bad is not None and "CASE" in bad


def test_genuinely_unknown_pack_asks_rather_than_guessing():
    qty, unit, per, how, bad = resolve_pack("Mystery Item", "10.00", basis="per_unit")
    assert qty is None and bad and "confirm once" in bad
