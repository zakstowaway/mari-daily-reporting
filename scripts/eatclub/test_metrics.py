"""Tests seeded with REAL measured numbers (ARCHITECTURE.md rule 2).

The cannibalisation cases are the actual Harry Gatos nights pulled 2026-07-18;
if the maths drifts, these break with numbers a human recognises.
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))

import config  # noqa: E402
import metrics  # noqa: E402


# --- contribution ----------------------------------------------------------- #

def test_contribution_single_bill_round_numbers():
    # $110 inc-GST bill, 20% offer, 22% blended COGS.
    c = metrics.contribution_for_bill(bill_inc=110, offer_pct=Decimal("0.20"),
                                      cost_blend=Decimal("0.22"))
    assert c.menu_ex == Decimal("100.00")
    assert c.discount_ex == Decimal("20.00")
    assert c.commission_ex == Decimal("10.00")
    assert c.net_ex == Decimal("70.00")
    assert c.cogs_ex == Decimal("22.00")
    assert c.contribution == Decimal("48.00")
    assert c.contrib_pct_of_net == Decimal("68.6")


def test_weekly_contribution_skips_unredeemed():
    rows = [
        {"bill_full": "110.00", "offer_pct": "20"},
        {"bill_full": "", "offer_pct": "25"},          # unredeemed -> skipped
        {"bill_full": None, "offer_pct": "25"},        # unredeemed -> skipped
    ]
    c = metrics.weekly_contribution(rows, cost_blend=Decimal("0.22"))
    assert c.menu_ex == Decimal("100.00")
    assert c.contribution == Decimal("48.00")


def test_offer_pct_accepts_percentage_or_fraction():
    a = metrics.contribution_for_bill(110, 20, Decimal("0.22"))
    b = metrics.contribution_for_bill(110, Decimal("0.20"), Decimal("0.22"))
    assert a == b


# --- cannibalisation: real HG nights, 2026-07-18 pull ----------------------- #

def test_hg_fri_17_jul_no_cannibalisation():
    r = metrics.assess_dinein(
        window_incgst="3558.17", eatclub_bills_incgst="721.36",
        baseline_incgst="2664.11", offer_tier_standard=True,
        early_window_weak=False, demand_shock=False)
    assert r.full_price_window == Decimal("2836.81")
    assert r.delta == Decimal("172.70")
    assert r.delta_pct == Decimal("6.5")
    assert r.breakeven_bills == Decimal("894.06")   # window - baseline
    assert r.verdict == metrics.NO_CANNIBALISATION


def test_hg_thu_9_jul_rescue_not_cannibalisation():
    # Full-price window fell below baseline, BUT the tier was lifted to 30% on a
    # weather-killed night -> RESCUE, not cannibalisation (Zak, 2026-07-11).
    r = metrics.assess_dinein(
        window_incgst="1289.10", eatclub_bills_incgst="581.60",
        baseline_incgst="1131.20", offer_tier_standard=False,
        early_window_weak=True, demand_shock=True)
    assert r.full_price_window < r.baseline_incgst
    assert r.verdict == metrics.RESCUE


def test_genuine_cannibalisation_signal():
    # Below baseline, standard tier, early window already fine, no shock.
    r = metrics.assess_dinein(
        window_incgst="2000", eatclub_bills_incgst="600",
        baseline_incgst="1800", offer_tier_standard=True,
        early_window_weak=False, demand_shock=False)
    assert r.full_price_window == Decimal("1400.00")
    assert r.verdict == metrics.SIGNAL


# --- takeaway substitution: Marilyna's ------------------------------------- #

def test_mari_substitution_when_flat():
    r = metrics.assess_takeaway(eatclub_incgst="300", delivery_incgst="700",
                                delivery_baseline="1000")
    assert r.total_offpremise == Decimal("1000.00")
    assert r.verdict == metrics.SUBSTITUTION


def test_mari_incremental_when_above():
    r = metrics.assess_takeaway(eatclub_incgst="400", delivery_incgst="900",
                                delivery_baseline="1000")
    assert r.delta_pct == Decimal("30.0")
    assert r.verdict == metrics.INCREMENTAL


# --- RG attribution mirrors daily_aggregator -------------------------------- #

def test_mari_rgs_route_to_marilynas():
    for rg in ["Dine-in Pizza", "Delivery Alcohol", "Marilyna's Pizza",
               "Add-ons - Pizza"]:
        assert config.is_marilynas_row(rg)
        assert not config.is_stowaway_proper_row(rg)


def test_delivery_cocktails_is_stowaway_not_mari():
    # Removed from MARILYNAS_RGS 2026-07-16 — it's the bar's revenue.
    assert not config.is_marilynas_row("Delivery Cocktails")
    assert config.is_stowaway_proper_row("Delivery Cocktails")


def test_hg_food_on_stow_till_is_neither():
    assert not config.is_marilynas_row("Harry Gatos Food")
    assert not config.is_stowaway_proper_row("Harry Gatos Food")


def test_harrys_suffix_stripped():
    # ' [harrys]' suffix is normalised away, same as the aggregator.
    assert config.is_stowaway_proper_row("Cocktails [Harrys]")


def test_60_banquet_product_override():
    assert config.is_marilynas_row("Other", product_name="$60 BANQUET")
