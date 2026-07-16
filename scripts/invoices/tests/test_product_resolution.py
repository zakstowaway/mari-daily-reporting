"""
Product resolution — guards the hardest lesson from the real-invoice comparison.

Appendix B: "Match on brand + product type + size."
That rule is BROKEN and will silently mis-map stock. Proven against
invoice 03729959 vs PO 54361209.

These tests exist to stop anyone (including a future me) from "simplifying"
resolution back to description matching because it looks like it would work.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="module")
def config():
    p = Path(__file__).resolve().parents[1] / "suppliers.yaml"
    return yaml.safe_load(p.read_text())


def _tokens(s):
    import re
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) > 2}


# (ILG code, invoice description, real Lightspeed product) — all verified.
REAL_PAIRS = [
    ("122-2867", "ALEHOUSE CRISP KEG", "Alehouse Summer Mid [Keg]"),
    ("122-2858", "ALEHOUSE PREMIUM KEG", "Alehouse Draught Lager [Keg]"),
    ("460-1639", "COKE NO SUGAR 1.25 LITRE", "Coke Zero 1.25L"),
]


# Tokens that identify the brand/form but do NOT identify WHICH product.
GENERIC = {"alehouse", "keg", "coke", "bottle", "litre", "tin", "can"}


@pytest.mark.parametrize("code,invoice_desc,ls_name", REAL_PAIRS)
def test_description_matching_fails_on_real_data(code, invoice_desc, ls_name):
    """
    The evidence.

    These pairs DO share generic tokens ("alehouse", "keg") — which is exactly
    what makes fuzzy matching dangerous: it scores a partial hit and looks
    like it worked. What matters is the DISTINGUISHING words, and those share
    nothing at all.

        ALEHOUSE CRISP KEG      -> distinguishing: {crisp}
        Alehouse Summer Mid[Keg]-> distinguishing: {summer, mid}
        intersection: {} — nothing to match on.
    """
    inv = _tokens(invoice_desc) - GENERIC
    ls = _tokens(ls_name) - GENERIC
    assert inv, f"no distinguishing tokens in {invoice_desc!r}"
    assert ls, f"no distinguishing tokens in {ls_name!r}"
    assert not (inv & ls), (
        f"expected NO distinguishing overlap between {invoice_desc!r} and "
        f"{ls_name!r}, got {inv & ls}"
    )


def test_the_two_alehouse_kegs_are_indistinguishable_by_brand_and_type():
    """
    THE DANGEROUS CASE. Both ILG lines are "ALEHOUSE * KEG". Brand matching
    alone cannot tell them apart, and they are DIFFERENT products at
    DIFFERENT prices ($184.94 vs $212.44 per keg).

    A fuzzy matcher would have a coin-flip between Summer Mid and Draught
    Lager — and a wrong pick is silent, since both are plausible kegs.
    """
    a = _tokens("ALEHOUSE CRISP KEG")
    b = _tokens("ALEHOUSE PREMIUM KEG")
    assert a & b == {"alehouse", "keg"}     # identical but for one word
    # ...and their real targets share nothing that would break the tie:
    assert not (_tokens("Alehouse Summer Mid [Keg]") & {"crisp"})
    assert not (_tokens("Alehouse Draught Lager [Keg]") & {"premium"})


def test_same_supplier_code_has_different_names_per_venue(config):
    """
    ILG 122-2858 is "Alehouse Draught Lager [Keg]" at Stowaway and
    "Alehouse Premium Lager [Keg]" at Harry Gatos. Appendix A documents
    venue-specific ProductIDs; names diverge too. A single global
    description->product map is therefore impossible.
    """
    m = config["product_resolution"]["ilg_codes"]["122-2858"]
    assert m["stowaway"] != m["harry_gatos"]


def test_config_forbids_fuzzy_matching(config):
    pr = config["product_resolution"]
    assert pr["strategy"] == "supplier_code_first"
    assert pr["fuzzy_description_matching"] == "forbidden"


def test_every_line_of_the_real_invoice_has_a_code_mapping(config):
    """All 14 lines of 03729959 resolve by code."""
    codes = config["product_resolution"]["ilg_codes"]
    for c in ["175-0420", "395-6785P", "305-1949P", "360-1310", "345-5638P",
              "122-2867", "122-2858", "115-3762", "117-4213", "460-1504",
              "460-2567", "460-1639", "450-1293", "460-3254"]:
        assert c in codes, f"missing mapping for {c}"


def test_beer_pack_sizes_are_24_not_4_or_6(config):
    """
    Zak, 16 Jul 2026: Stowaway/HG stock SINGLE units, not 4- or 6-packs.
    So Corona and Heaps resolve at pack 24 — which is why ILG's LUC
    (per 6-pack / per 4-pack) is unusable and TOT/(qty*24) is correct.
    """
    codes = config["product_resolution"]["ilg_codes"]
    assert codes["115-3762"]["pack"] == 24   # Corona  -> 61.71/24 = $2.57
    assert codes["117-4213"]["pack"] == 24   # Heaps   -> 64.07/24 = $2.67
    assert codes["115-3762"]["basis"] == "per_can"
    assert codes["117-4213"]["basis"] == "per_can"
