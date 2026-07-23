#!/usr/bin/env python3
"""Schema guard for the daily-history CSVs.

Compares the WORKING-TREE version of each data/<venue>_daily_history.csv against
the version committed at git HEAD and FAILS the run if a regression is detected:

  1. DROPPED COLUMN     - a header column present at HEAD is missing now.
  2. LOST DATES         - a date row present at HEAD is missing now (truncation).
  3. COLUMN WENT DARK   - a column that carried non-empty, non-zero values at HEAD
                          now has none over the overlapping dates (a silent wipe,
                          e.g. the leave_dollars regression).

This is the check that would have caught both the history truncation ("last week
is 6 days now") and the leave wipe before they were committed.

Exit codes: 0 = ok, 2 = regression (hard stop). Wire into daily_pull.yml BEFORE
the commit/push step. A legitimate schema change is acknowledged with
--allow "venue:column" (repeatable) or --allow-drop for an intentional removal.

Run: python3 scripts/schema_guard.py            # guard all venue histories
     python3 scripts/schema_guard.py --allow mari:leave_dollars
"""
import argparse, csv, io, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENUES = ["stow", "hg", "mari"]
# Columns whose disappearance is always a regression worth blocking on. (Any
# dropped column blocks; these are additionally checked for "went dark".)
WATCH = ["revenue_ex_gst", "wages_dollars", "cogs_dollars", "delivery_dollars",
         "leave_dollars", "wages_admin_dollars", "wages_driver_dollars",
         "food_ex_gst", "bev_ex_gst"]


def head_version(relpath):
    """The file contents at git HEAD, or None if the file is new/untracked."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{relpath}"],
                             capture_output=True, text=True)
        return out.stdout if out.returncode == 0 else None
    except Exception:
        return None


def load(text):
    r = csv.DictReader(io.StringIO(text))
    rows = list(r)
    return r.fieldnames or [], rows


def nonzero(v):
    if v is None:
        return False
    s = str(v).strip()
    if s == "":
        return False
    try:
        return float(s) != 0.0
    except ValueError:
        return True  # non-numeric, non-empty (alerts etc.) counts as "present"


def check_venue(venue, allow):
    rel = f"data/{venue}_daily_history.csv"
    path = ROOT / rel
    if not path.exists():
        return []
    head_text = head_version(rel)
    if head_text is None:
        return []   # new file, nothing to compare
    old_cols, old_rows = load(head_text)
    new_cols, new_rows = load(path.read_text())
    problems = []

    # 1. dropped columns
    dropped = [c for c in old_cols if c not in new_cols]
    for c in dropped:
        if f"{venue}:{c}" not in allow and "--allow-drop" not in allow:
            problems.append(f"{venue}: column '{c}' was DROPPED from the header")

    # 2. lost dates
    old_dates = {r["date"] for r in old_rows if r.get("date")}
    new_dates = {r["date"] for r in new_rows if r.get("date")}
    lost = sorted(old_dates - new_dates)
    if lost:
        show = ", ".join(lost[:6]) + (" …" if len(lost) > 6 else "")
        problems.append(f"{venue}: {len(lost)} date row(s) LOST (truncation): {show}")

    # 3. column went dark, over the overlapping dates only
    common = old_dates & new_dates
    new_by_date = {r["date"]: r for r in new_rows}
    old_by_date = {r["date"]: r for r in old_rows}
    for c in WATCH:
        if c not in old_cols or c not in new_cols:
            continue
        if f"{venue}:{c}" in allow:
            continue
        old_hits = sum(1 for d in common if nonzero(old_by_date[d].get(c)))
        new_hits = sum(1 for d in common if nonzero(new_by_date[d].get(c)))
        if old_hits >= 3 and new_hits == 0:
            problems.append(
                f"{venue}: column '{c}' went DARK — {old_hits} non-zero values at HEAD, "
                f"0 now (over {len(common)} shared dates)")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow", action="append", default=[],
                    help="acknowledge an intentional change, e.g. mari:leave_dollars")
    ap.add_argument("--allow-drop", action="store_true", help="permit column drops")
    args = ap.parse_args()
    allow = set(args.allow)
    if args.allow_drop:
        allow.add("--allow-drop")

    all_problems = []
    for v in VENUES:
        all_problems += check_venue(v, allow)

    if all_problems:
        print("SCHEMA GUARD FAILED — the daily-history CSVs regressed:")
        for p in all_problems:
            print(f"  ✗ {p}")
        print("\nFix at SOURCE (generator / register), not by editing the CSV. If the "
              "change is intentional, re-run with --allow 'venue:column' or --allow-drop.")
        sys.exit(2)
    print("schema guard: ok (no dropped columns, lost dates, or dark columns)")


if __name__ == "__main__":
    main()
