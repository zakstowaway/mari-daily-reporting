#!/usr/bin/env python3
"""Upsert one Uber Direct daily fee into data/uber_direct_daily.csv.

Called by .github/workflows/uber_direct_dispatch.yml, which is fired by the
Pipedream "Mari Insights to GitHub" workflow when Uber's daily Direct invoice
email arrives (event_type: uber-direct-arrived, client_payload: target_date + fee).

Usage: uber_direct_upsert.py <YYYY-MM-DD> <fee_inc_gst>
Idempotent: re-running for the same date overwrites that date's row.
"""
import sys, csv, os
from datetime import datetime

HEADER = ["date", "shop", "fee_inc_gst", "source"]
SHOP = "mari"
SOURCE = "uber_direct_email"
PATH = os.path.join(os.path.dirname(__file__), "..", "data", "uber_direct_daily.csv")


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: uber_direct_upsert.py <YYYY-MM-DD> <fee_inc_gst>")
    date = sys.argv[1].strip()
    datetime.strptime(date, "%Y-%m-%d")            # validate the date
    fee = f"{float(sys.argv[2]):.2f}"              # validate + normalise the amount
    if float(fee) < 0:
        sys.exit(f"refusing negative fee: {fee}")

    rows = []
    if os.path.exists(PATH):
        with open(PATH, newline="") as f:
            r = list(csv.reader(f))
        if r and r[0] == HEADER:
            rows = r[1:]

    # drop any existing row for this (date, shop), then add the new one
    rows = [row for row in rows if not (len(row) >= 2 and row[0] == date and row[1] == SHOP)]
    rows.append([date, SHOP, fee, SOURCE])
    rows = [row for row in rows if row and any(c.strip() for c in row)]
    rows.sort(key=lambda x: (x[0], x[1]))

    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    print(f"upserted {date} {SHOP} {fee}")


if __name__ == "__main__":
    main()
