"""Give-away tests, seeded with the real Stowaway 2026-07-14 redemptions.

That day: 3 PAID tables, 4 covers, menu $320.68 inc, net $214.21 inc ->
give-away $106.47 inc. This is the exact figure the aggregator subtracts, and
it moved Stowaway's GP from an overstated 78.2% to the true 77.1%.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import giveaway  # noqa: E402

JUL14 = [
    {"date": "2026-07-14", "venue": "Stowaway Bar", "party_size": "1",
     "offer_pct": "25", "bill_full": "81.97", "net_revenue": "56.95", "status": "PAID"},
    {"date": "2026-07-14", "venue": "Stowaway Bar", "party_size": "1",
     "offer_pct": "25", "bill_full": "59.17", "net_revenue": "37.86", "status": "PAID"},
    {"date": "2026-07-14", "venue": "Stowaway Bar", "party_size": "2",
     "offer_pct": "25", "bill_full": "179.54", "net_revenue": "119.40", "status": "PAID"},
]


def test_real_jul14():
    g = giveaway.day_giveaway(JUL14, "2026-07-14", "Stowaway Bar")
    assert g["tables"] == 3
    assert g["covers"] == 4
    assert g["menu_inc"] == 320.68
    assert g["net_inc"] == 214.21
    assert g["giveaway_inc"] == 106.47
    # discount = 25% of each bill; commission = the rest of the give-away
    assert g["discount_inc"] == 80.17
    assert g["commission_inc"] == 26.30


def test_unredeemed_and_offerless():
    rows = [
        # UNREDEEMED (no bill) -> ignored entirely
        {"party_size": "2", "offer_pct": "30", "bill_full": "", "net_revenue": "", "status": "UNREDEEMED"},
        # offerless (0%) PAID -> only the ~11% commission is given away
        {"party_size": "1", "offer_pct": "0", "bill_full": "45.34", "net_revenue": "40.36", "status": "PAID"},
    ]
    g = giveaway.day_giveaway(rows, "2026-07-17", "Stowaway Bar")
    assert g["tables"] == 1
    assert g["covers"] == 1
    assert g["giveaway_inc"] == 4.98
    assert g["discount_inc"] == 0.0
    assert g["commission_inc"] == 4.98


def test_dollar_and_comma_cleaning():
    rows = [{"party_size": "2", "offer_pct": "30", "bill_full": "$1,090.00",
             "net_revenue": "$643.10", "status": "PAID"}]
    g = giveaway.day_giveaway(rows, "2026-07-18", "Stowaway Bar")
    assert g["menu_inc"] == 1090.00
    assert g["giveaway_inc"] == 446.90
