"""Record weekly Uber Direct fee totals for Marilyna's.

Uber Direct (Uber's fleet delivering MARI-branded orders) is a per-order fee
that no pipeline pulls automatically yet. Zak enters a weekly total from the
Uber merchant portal; the daily aggregator amortizes it across the 7 days of
that week (week ends SUNDAY) into the delivery lane.

Usage:
  python scripts/enter_uber_direct.py 2026-07-12 184.50
      -> records $184.50 for the week ending Sunday 2026-07-12
  python scripts/enter_uber_direct.py --list
      -> show all recorded weeks

Any date inside the week works — it's snapped to that week's Sunday.
After entering, re-run the aggregator for the affected days (or just let the
next daily run pick it up for the current week):
  python scripts/daily_aggregator.py --venue marilynas 2026-07-08
"""
import json, os, sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(os.environ.get("REPO_ROOT", "."))
FILE = REPO_ROOT / "data" / "manual" / "uber_direct.json"


def load() -> dict:
    if FILE.exists():
        with FILE.open() as f:
            return json.load(f)
    return {
        "_comment": "Weekly Uber Direct fee totals (dollars, inc GST), keyed by week-ending Sunday. "
                    "Entered manually via scripts/enter_uber_direct.py until an Uber Direct API feed exists. "
                    "daily_aggregator.py amortizes each week's total across its 7 days.",
        "weeks": {},
    }


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    data = load()
    if args[0] == "--list":
        for wk, amt in sorted(data.get("weeks", {}).items()):
            print(f"  week ending {wk}: ${amt:,.2f}")
        if not data.get("weeks"):
            print("  (no weeks recorded)")
        return
    if len(args) != 2:
        sys.exit("Usage: enter_uber_direct.py <any-date-in-week> <weekly-total-dollars>  (or --list)")
    d = date.fromisoformat(args[0])
    amount = float(args[1].replace("$", "").replace(",", ""))
    week_ending = d + timedelta(days=(6 - d.weekday()))  # snap to Sunday
    data.setdefault("weeks", {})[week_ending.isoformat()] = round(amount, 2)
    FILE.parent.mkdir(parents=True, exist_ok=True)
    with FILE.open("w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    print(f"Recorded ${amount:,.2f} for week ending Sunday {week_ending}")
    print(f"-> {FILE}")
    print("Re-run daily_aggregator.py for the affected dates to fold it into the dashboard.")


if __name__ == "__main__":
    main()
