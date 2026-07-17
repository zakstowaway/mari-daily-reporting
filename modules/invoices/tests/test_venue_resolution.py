"""
Venue resolution — guards the kitchen's equivalent of the LUC trap.

Appendix A gives ONE global priority list for venue signals. Verified WRONG
16 Jul 2026 against two real Select Fresh invoices from the same day.

Misresolution is silent and expensive: stock lands in the wrong venue, and per
Appendix A a cost update against the wrong venue's ProductIDs does NOTHING.
Select Fresh alone is $302,013 across 2,852 invoices.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root


@pytest.fixture(scope="module")
def config():
    p = Path(__file__).resolve().parents[1] / "suppliers.yaml"
    return yaml.safe_load(p.read_text())


# Verbatim from the two real invoices, same supplier, same day (15 Jul 2026).
SELECT_FRESH_HG = {
    "invoice": "3084903", "total": "95.60",
    "customer": "HARRY GATTOS",                      # misspelled by the supplier
    "address": "LVL 1, SHP 18, 1-3 MOORE ROAD",
    "delivery_code": "182096#",
    "delivery_instructions": "BAR AT STOWAWAY",      # says STOWAWAY on an HG invoice
    "account_code": "HARGAT",
    "truth": "harry_gatos",
}
SELECT_FRESH_SB = {
    "invoice": "3085647", "total": "110.60",
    "customer": "STOWAWAY",
    "address": "LVL 1, SHP 18, 1-3 MOORE ROAD",      # IDENTICAL to the HG one
    "delivery_code": "182096#",                      # IDENTICAL to the HG one
    "delivery_instructions": "",
    "account_code": "STOWA",
    "truth": "stowaway",
}


def test_address_is_identical_across_venues_so_cannot_resolve():
    """
    Appendix A: "Delivery address — Shop 18/1-3 Moore Rd = Stowaway."
    Both Select Fresh invoices carry the SAME address. It resolves nothing.
    """
    assert SELECT_FRESH_HG["address"] == SELECT_FRESH_SB["address"]


def test_182096_appears_on_both_venues_so_is_not_a_venue_code():
    """
    Appendix A: "code 182096# ... = Harry Gatos."
    It is on the STOWAWAY invoice too — it's Select Fresh's GROUP account ref.
    Treating it as a venue code sends every Stowaway produce invoice to HG.
    """
    assert SELECT_FRESH_HG["delivery_code"] == SELECT_FRESH_SB["delivery_code"] == "182096#"


def test_delivery_instructions_actively_lie():
    """
    The Harry Gatos invoice reads "BAR AT STOWAWAY" (HG is upstairs; produce
    drops at the bar). Any text match on venue names resolves it BACKWARDS.
    """
    assert "STOWAWAY" in SELECT_FRESH_HG["delivery_instructions"]
    assert SELECT_FRESH_HG["truth"] == "harry_gatos"


def test_account_code_is_the_only_discriminator():
    """The one field that actually differs."""
    assert SELECT_FRESH_HG["account_code"] != SELECT_FRESH_SB["account_code"]
    assert {SELECT_FRESH_HG["account_code"], SELECT_FRESH_SB["account_code"]} == {"HARGAT", "STOWA"}


@pytest.mark.parametrize("inv", [SELECT_FRESH_HG, SELECT_FRESH_SB])
def test_config_resolves_both_select_fresh_invoices_correctly(config, inv):
    sigs = config["venue_resolution"]["by_supplier"]["select_fresh"]["account_codes"]
    assert sigs[inv["account_code"]] == inv["truth"]


def test_select_fresh_explicitly_ignores_the_misleading_signals(config):
    ig = config["venue_resolution"]["by_supplier"]["select_fresh"]["ignore_signals"]
    for s in ("address", "182096#", "delivery_instructions"):
        assert s in ig


def test_ilg_and_select_fresh_use_different_signal_sets(config):
    """
    The core lesson: signals are PER-SUPPLIER. ILG uses numeric account codes
    (2428/3622), Select Fresh uses alpha ones (STOWA/HARGAT). Applying one
    supplier's rule to another silently misroutes.
    """
    by = config["venue_resolution"]["by_supplier"]
    ilg = set(by["ilg"]["account_codes"])
    sf = set(by["select_fresh"]["account_codes"])
    assert not (ilg & sf), "signal sets must not overlap"
    assert ilg == {"2428", "3622"}
    assert sf == {"STOWA", "HARGAT"}


def test_address_fragments_are_not_used_globally(config):
    """
    The old global address_fragments map is gone. Any future entry must be
    proven per-supplier first.
    """
    vr = config["venue_resolution"]
    assert "address_fragments" not in vr
    assert vr["address_fragments_verified_per_supplier"] in ({}, None)


def test_unresolved_venue_blocks(config):
    """Never guess a venue. NO_VENUE is an ERROR in the validator."""
    assert config["venue_resolution"]["unresolved_action"] == "block"
