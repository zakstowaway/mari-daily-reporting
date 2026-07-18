"""EatClub give-away — the money EatClub keeps that the POS never sees.

EatClub tables ring the FULL bill on the POS at full price. EatClub then keeps
the offer discount + an 11% commission (10% ex-GST + GST) and settles the NET.
So Lightspeed/Insights revenue is OVERSTATED by (bill - net) per redeemed table,
which overstates reported margin.

This turns a day's EatClub redemptions into the single scalar the daily
aggregator needs to correct that: `giveaway_inc = sum(bill_full - net_revenue)`
over PAID tables. Written as `data/eatclub_{prefix}_{date}.json`; the aggregator
subtracts it from revenue (see daily_aggregator.py, "EatClub give-away").

Money is float here to match daily_aggregator.py (that file has no Decimal).
"""

from __future__ import annotations

import csv
import json
import os

VENUE_PREFIX = {"stowaway": "stow", "harry": "hg", "marilynas": "mari"}


def _f(x):
    s = str(x if x is not None else "").replace("$", "").replace(",", "").strip()
    return float(s) if s else 0.0


def day_giveaway(rows, date, venue):
    """Reduce one day's EatClub rows to the give-away fact.

    rows: dicts with bill_full, net_revenue, party_size, offer_pct, status.
    Only PAID rows with a bill count (UNREDEEMED offers cost nothing). Returns the
    dict written to data/eatclub_{prefix}_{date}.json.
    """
    covers = 0
    menu_inc = net_inc = discount_inc = 0.0
    paid = 0
    for r in rows:
        if (r.get("status") or "").upper() != "PAID":
            continue
        bill = _f(r.get("bill_full"))
        if bill <= 0:
            continue
        paid += 1
        covers += int(_f(r.get("party_size")) or 0)
        net = _f(r.get("net_revenue"))
        off = _f(r.get("offer_pct"))
        off = off / 100 if off > 1 else off
        menu_inc += bill
        net_inc += net
        discount_inc += bill * off
    giveaway_inc = menu_inc - net_inc
    return {
        "date": date,
        "venue": venue,
        "tables": paid,
        "covers": covers,
        "menu_inc": round(menu_inc, 2),
        "net_inc": round(net_inc, 2),
        "giveaway_inc": round(giveaway_inc, 2),        # the aggregator reads this
        "discount_inc": round(discount_inc, 2),         # offer discount portion
        "commission_inc": round(giveaway_inc - discount_inc, 2),  # ~11% commission
    }


def write_from_transactions(transactions_csv, data_dir):
    """Group a per-venue EatClub transactions CSV by date and write one
    data/eatclub_{prefix}_{date}.json per trading day. Returns the paths written.
    """
    with open(transactions_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    by_key = {}
    for r in rows:
        venue = (r.get("venue") or "").strip()
        prefix = _prefix_for(venue)
        by_key.setdefault((prefix, venue, r["date"]), []).append(r)

    written = []
    for (prefix, venue, date), day_rows in sorted(by_key.items()):
        fact = day_giveaway(day_rows, date, venue)
        if fact["giveaway_inc"] <= 0:
            continue
        out = os.path.join(data_dir, f"eatclub_{prefix}_{date}.json")
        with open(out, "w") as fh:
            json.dump(fact, fh, indent=2)
        written.append(out)
    return written


def _prefix_for(venue):
    v = venue.lower()
    if "harry" in v:
        return "hg"
    if "marilyn" in v:
        return "mari"
    return "stow"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        raise SystemExit("usage: giveaway.py <transactions.csv> <data_dir>")
    for p in write_from_transactions(sys.argv[1], sys.argv[2]):
        print("wrote", p)
