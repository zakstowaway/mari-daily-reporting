"""
Unit tests for the salaried wage model — allocation, the roster stand-in, and
the under-40 shortfall rule.  wage_model.allocate_week only; no network.

    python3 scripts/test_wage_open_week.py      # exits 1 on failure

THE CANON, AND THE TWO WRONG TURNS EITHER SIDE OF IT
  A salaried person costs annual/52 for the week. The only question is where it
  LANDS. Three answers have been tried:

  1. hours x rate                (the original — WRONG, loses money)
        Kris logged 16h of a 40h paid week -> $719 booked, $1,798 actually paid.
        $268k of real labour vanished across 90 weeks.

  2. share = hours / hours_logged (the fix — conserved the money, misplaced it)
        Money correct, but a manager who clocked one 6h shift wore his ENTIRE
        week on it. Kris, Tue 14 Jul: $2,013.76 onto one shift against $2,148 of
        trade -> Stow read 100.9%. Renan, Wed 15 Jul -> Mari read 185.5%.

  3. share = hours / max(hours, 40), shortfall -> LEAVE   (Zak, 2026-07-17)
        Contracted to 40. Not on for 40 -> the balance is leave, which is what
        Xero shows (leave sits INSIDE the 40: Kris = 38.5 worked + 1.5 leave).
        Money conserved to the cent AND lands where it was earned.

  The trap: (3) LOOKS like (1) — both divide by 40. The difference is the
  remainder. (1) throws it away; (3) books it to leave. If you ever find
  yourself "simplifying" this back to hours/40 with no leave row, you have
  reinvented the $268k bug.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wage_model import allocate_week, CONTRACT_HOURS

WPY = 52
WEEK = [f"2026-07-{d:02d}" for d in range(13, 20)]
PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else '!! FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))

def shift(emp, d, bucket, hours=8, cost=0, roster=False):
    s = {"employee_id": emp, "hours": hours, "cost": cost, "date": d, "bucket": bucket}
    if roster: s["_roster"] = True
    return s

def split(costed):
    """(venue $, leave $, dropped roster $) — what actually gets booked where."""
    ven = sum(s["cost_final"] for s in costed if s["bucket"] != "leave" and not s.get("_roster"))
    lv  = sum(s["cost_final"] for s in costed if s["bucket"] == "leave")
    ros = sum(s["cost_final"] for s in costed if s.get("_roster"))
    return ven, lv, ros

KRIS = {"1": 93496}          # $1,798.00/wk ex-super
RENAN = {"142": 75000}       # $1,442.31/wk ex-super
KW, RW = 93496/52, 75000/52

print("=" * 78)
print("1. KRIS — one 6.075h shift, nothing else. The 100.9% Tuesday.")
c, w = allocate_week([shift("1", "2026-07-14", "stow|FOH", hours=6.075)], KRIS, WPY, week_days=WEEK)
ven, lv, _ = split(c)
check("venue gets 6.075/40, not 100%", abs(ven - KW * 6.075/40) < 0.01, f"${ven:,.2f}")
check("shortfall goes to LEAVE", abs(lv - KW * (40-6.075)/40) < 0.01, f"${lv:,.2f}")
check("money conserved to the cent", abs(ven + lv - KW) < 0.01, f"${ven+lv:,.2f} vs ${KW:,.2f}")
check("leave spread across the week, not spiked on one day",
      len({s["date"] for s in c if s["bucket"] == "leave"}) == 7)
check("a shortfall warning is raised", any(x["type"] == "salaried_shortfall_leave" for x in w))

print("\n2. NOT the old hours x rate bug — the remainder must not vanish")
check("nothing is lost", abs(ven + lv - KW) < 0.01)
check("leave is REAL money, not zero", lv > 1000, f"${lv:,.2f}")

print("\n3. OVER 40 — no leave, no cap")
c, w = allocate_week([shift("1", "2026-07-14", "stow|FOH", hours=45)], KRIS, WPY, week_days=WEEK)
ven, lv, _ = split(c)
check("all of it to the venue", abs(ven - KW) < 0.01, f"${ven:,.2f}")
check("no leave row", lv == 0)
check("no shortfall warning", not any(x["type"] == "salaried_shortfall_leave" for x in w))

print("\n4. EXACTLY 40 — the boundary")
c, _ = allocate_week([shift("1", "2026-07-14", "stow|FOH", hours=40)], KRIS, WPY, week_days=WEEK)
ven, lv, _ = split(c)
check("all venue, no leave", abs(ven - KW) < 0.01 and lv == 0, f"venue ${ven:,.2f} leave ${lv:,.2f}")

print("\n5. ROSTER STAND-INS count toward the 40")
logged = [shift("142", "2026-07-15", "mari|Kitchen")]
roster = [shift("142", d, "stow|Kitchen", roster=True) for d in ["2026-07-17","2026-07-18","2026-07-19"]]
c, _ = allocate_week(logged + roster, RENAN, WPY, week_days=WEEK)
ven, lv, ros = split(c)
check("the worked day books 8/40, not 8/8", abs(ven - RW * 8/40) < 0.01, f"${ven:,.2f}")
check("rostered days sized but NOT booked", abs(ros - RW * 24/40) < 0.01, f"${ros:,.2f}")
check("32 rostered of 40 -> 8h leave", abs(lv - RW * 8/40) < 0.01, f"${lv:,.2f}")
check("venue + roster + leave == the whole week", abs(ven + ros + lv - RW) < 0.01)

print("\n6. STABILITY — the day's number must not move as the week fills")
seen = []
for n in (1, 2, 3, 4):
    lg = [shift("142", f"2026-07-{13+i}", "mari|Kitchen") for i in range(n)]
    rs = [shift("142", f"2026-07-{13+i}", "stow|Kitchen", roster=True) for i in range(n, 4)]
    c, _ = allocate_week(lg + rs, RENAN, WPY, week_days=WEEK)
    per_day = sum(s["cost_final"] for s in c
                  if s["bucket"] == "mari|Kitchen" and not s.get("_roster")) / n
    seen.append(round(per_day, 6))
check("1,2,3,4 days worked -> identical per-day cost", len(set(seen)) == 1, f"{seen[0]:.2f}/day")

print("\n7. HOURLY staff are untouched by any of this")
c, _ = allocate_week([shift("999", "2026-07-15", "stow|FOH", hours=8, cost=210.0)], {}, WPY, week_days=WEEK)
ven, lv, _ = split(c)
check("keeps Deputy's own Cost", abs(ven - 210.0) < 0.01, f"${ven:,.2f}")
check("no leave invented for casuals", lv == 0)

print("\n8. TWO VENUES — leave comes out before the split, not after")
c, _ = allocate_week([shift("1", "2026-07-14", "stow|FOH", hours=10),
                      shift("1", "2026-07-15", "hg|Bar", hours=10)], KRIS, WPY, week_days=WEEK)
ven, lv, _ = split(c)
check("20h of 40 -> half to venues, half to leave",
      abs(ven - KW/2) < 0.01 and abs(lv - KW/2) < 0.01, f"venue ${ven:,.2f} leave ${lv:,.2f}")
check("still conserved", abs(ven + lv - KW) < 0.01)

print("\n" + "=" * 78)
print(f"PASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL: print("FAILURES: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
