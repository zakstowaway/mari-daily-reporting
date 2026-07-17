"""Restate the Mari<->Stow revenue boundary across history from Lightspeed's own
reporting groups.

WHY THIS EXISTS
---------------
Marilyna's has no till. Her "own" CSV was only ever a FILTER over the Stow POS —
a saved Lightspeed schedule holding a list of reporting groups. Stow then
stripped her rows out by CLASSIFIER (MARILYNAS_RGS in daily_aggregator). Two
definitions of one boundary, and any daylight between them leaked money one way
or double-counted it the other.

daily_aggregator was fixed on 2026-07-17 to source Mari FROM the Stow till, so
the two definitions became one and the leak became unreachable. But that only
governs days pulled from 2026-07-06 on. The 549 days before it were split by the
old, drifting definition and can never be re-pulled — the Insights exports age
out after ~10 days.

Lightspeed still holds the reporting-group truth for every one of those days.
This script uses it.

WHAT IT DOES / DOESN'T DO
-------------------------
  mari_new = Lightspeed's own 'm' reporting groups, exactly.
  stow_new = (stow_old + mari_old) - mari_new

The PAIR TOTAL IS CONSERVED to the cent. This is a boundary correction: the
money was always in the business, it was on the wrong venue's line. The group
number does not move; Mari's and Stow's individual revenue, GP% and wage% do.

It does NOT touch: wages, delivery, uber, leave, or Harry Gatos. It does not
import Lightspeed's cost as an absolute (see COGS below).

  --write   actually save. Default is a dry run.

WHY THE RESIDUAL GOES TO STOW, NOT PRO-RATA
-------------------------------------------
Lightspeed's pair total and our stored pair total disagree by $2.40/day on
average ($-1,313.63 over 549 days, 0.02% of revenue) — export vintage, late-
closed orders, whatever. That gap is NOT what this script is chartered to fix,
so it is preserved, not spread. Mari is set exactly to Lightspeed and Stow
absorbs the residual: Stow is ~4x her size, so the same dollar distorts her four
times as much. Verified: the largest single-day residual is $188.90 (2026-06-25,
already flagged as an anomaly).

COGS
----
Stored cogs / Lightspeed Cost Ex Tax = 0.909 = 1/1.1 EXACTLY on clean days —
stored cogs is inc-GST, LS cost is ex. So the relationship is known, and Mari's
share of cost can be lifted from Lightspeed and grossed by 1.1.

Zak, 2026-07-17: "estimated cogs from lightspeed wont be possible as we only
inputted recipes for food recently." That is why LS cost is used ONLY to place
Mari's share and never as the pair total — the pair total stays exactly what we
stored. If Lightspeed's recipes have been revised since, that revision does not
leak into history.

Lightspeed's cost also contains real garbage: 9 RG-days where cost > 5x revenue,
worst $1,716,012.34 of 'tap beer' on 2025-09-19 (the Serpents Kiss Schooner
recipe typo daily_aggregator's row_cogs() guards against). ALL NINE are bar-side;
zero are in a Mari reporting group. Checked, not assumed — and it is the reason
mari-from-LS + stow-as-residual is safe here: the pollution is in the residual,
which we overwrite with a number that never touched it.
"""
import csv, json, shutil, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LS_FILE = Path(sys.argv[sys.argv.index("--ls") + 1]) if "--ls" in sys.argv else Path("/tmp/ls_rg_cost.csv")
WRITE = "--write" in sys.argv
CUT = "2026-07-06"          # from here the aggregator sources Mari from the till
GST = 1.1

# ---- ONE definition of the boundary, or none ----
#
# These MUST be daily_aggregator's own sets. A copy here would drift, and a
# drifting boundary is the exact defect this script exists to repair.
#
# But `import daily_aggregator` is not an option: that module does its work at
# import time — it parses argv and runs a whole day's aggregation as a side
# effect — so importing it here would silently kick off an unrelated job.
#
# So: read its constants out of the source with ast.literal_eval. No execution,
# no copy, and if someone renames or restructures those assignments this exits
# loudly instead of quietly backfilling 549 days with a stale boundary.
import ast   # noqa: E402

_AGG = ROOT / "scripts" / "daily_aggregator.py"


def _const(name):
    tree = ast.parse(_AGG.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    sys.exit(f"Could not find {name} in {_AGG}. The boundary is defined there and "
             f"nowhere else — this script will not guess it.")


MARILYNAS_RGS = _const("MARILYNAS_RGS")
FOOD_RGS = _const("FOOD_RGS")
HG_FOOD_RG = _const("HG_FOOD_RG")


def _norm_rg(rg):
    k = (rg or "").strip().lower()
    return k[:-len(" [harrys]")] if k.endswith(" [harrys]") else k


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def load_ls():
    """Lightspeed RG x date, Stow till only -> {date: {bucket: {rev, cost}}}."""
    out = defaultdict(lambda: defaultdict(lambda: {"rev": 0.0, "cost": 0.0}))
    with LS_FILE.open() as f:
        rd = csv.DictReader(f)
        need = {"Products Reporting Group Name", "Sales Data Sale Closed Date",
                "Location Site Name", "Sales Data Cost Ex Tax", "Exclusive of tax"}
        missing = need - set(rd.fieldnames or [])
        if missing:
            sys.exit(f"{LS_FILE} is missing columns: {sorted(missing)}\n"
                     f"Expected an Insights salelines export with Inc Ex Tax Selector = Exclusive of tax.")
        for r in rd:
            if r["Location Site Name"] != "Stowaway Bar":
                continue
            rg = _norm_rg(r["Products Reporting Group Name"])
            if rg in MARILYNAS_RGS:
                b = "m"
            elif rg == HG_FOOD_RG:
                b = "hgf"       # HG's food on the Stow till — not ours to move
            elif rg in FOOD_RGS:
                b = "stow_food"
            else:
                b = "stow_bev"
            d = out[r["Sales Data Sale Closed Date"]][b]
            d["rev"] += float(r["Exclusive of tax"] or 0)
            d["cost"] += float(r["Sales Data Cost Ex Tax"] or 0)
    return out


def load_hist(p):
    with p.open() as f:
        rd = csv.DictReader(f)
        return list(rd), rd.fieldnames


def pct(a, b):
    return round(a / b * 100, 1) if b else None


def _fmt(v):
    """None -> '' so the CSV keeps its existing 'no data' convention."""
    return "" if v is None else f"{v}"


def _restate_wage_pcts(row, rev, food, bev):
    """Wage % = wage dollars / revenue. Revenue moved, so the % must move.

    The DOLLARS are Deputy's and are never touched here. Only rows that already
    carry a wage figure are restated — a blank stays blank rather than becoming
    a confident 0.0%.
    """
    def rp(key, dollars_key, denom):
        if row.get(key, "") == "" and row.get(dollars_key, "") == "":
            return
        d = num(row.get(dollars_key))
        row[key] = _fmt(pct(d, num(denom)))

    rp("wages_pct", "wages_dollars", rev)
    rp("wages_kitchen_pct", "wages_kitchen_dollars", food)
    rp("wages_foh_pct", "wages_foh_dollars", bev)


ls = load_ls()
mari_rows, mari_fields = load_hist(DATA / "mari_daily_history.csv")
stow_rows, stow_fields = load_hist(DATA / "stow_daily_history.csv")
mari_by = {r["date"]: r for r in mari_rows}
stow_by = {r["date"]: r for r in stow_rows}

dates = sorted(d for d in (set(ls) & set(mari_by) & set(stow_by)) if d < CUT)
print(f"Lightspeed file : {LS_FILE}")
print(f"Days to restate : {len(dates)} (all < {CUT})")
print(f"Mode            : {'WRITE' if WRITE else 'DRY RUN — nothing will be saved'}\n")

changes = []
group_before = group_after = 0.0
skipped = []

for d in dates:
    m, s = mari_by[d], stow_by[d]
    m_rev_old, s_rev_old = num(m["revenue_ex_gst"]), num(s["revenue_ex_gst"])
    pair_rev = m_rev_old + s_rev_old
    m_cogs_old, s_cogs_old = num(m["cogs_dollars"]), num(s["cogs_dollars"])
    pair_cogs = m_cogs_old + s_cogs_old

    day = ls[d]
    m_rev_new = round(day["m"]["rev"], 2)

    # Refuse to invent a negative Stow. If Lightspeed says Mari alone out-earned
    # the pair we stored, something is wrong upstream of this script and the day
    # is left exactly as it is rather than "fixed" into nonsense.
    if m_rev_new > pair_rev + 0.01:
        skipped.append((d, "mari_ls > stored pair", m_rev_new, pair_rev))
        continue

    s_rev_new = round(pair_rev - m_rev_new, 2)

    # Mari's cost from Lightspeed, grossed to inc-GST to match how we store it.
    # Clamped to the pair so Stow can never go negative on the cost line either.
    m_cogs_new = round(min(day["m"]["cost"] * GST, pair_cogs), 2)
    s_cogs_new = round(pair_cogs - m_cogs_new, 2)

    # Stow's food/bev split, taken from Lightspeed's own RG shares and scaled to
    # the Stow revenue we just derived, so the two slices still sum to the whole.
    ls_stow_rev = day["stow_food"]["rev"] + day["stow_bev"]["rev"]
    if ls_stow_rev > 0:
        f_share = day["stow_food"]["rev"] / ls_stow_rev
        s_food_new = round(s_rev_new * f_share, 2)
        s_bev_new = round(s_rev_new - s_food_new, 2)
    else:
        s_food_new = s_bev_new = 0.0

    ls_stow_cost = day["stow_food"]["cost"] + day["stow_bev"]["cost"]
    if ls_stow_cost > 0:
        fc_share = day["stow_food"]["cost"] / ls_stow_cost
        s_food_cogs = round(s_cogs_new * fc_share, 2)
        s_bev_cogs = round(s_cogs_new - s_food_cogs, 2)
    else:
        s_food_cogs = s_bev_cogs = 0.0

    moved = m_rev_new - m_rev_old
    group_before += pair_rev
    group_after += m_rev_new + s_rev_new
    if abs(moved) < 0.005 and abs(m_cogs_new - m_cogs_old) < 0.005:
        continue
    changes.append({
        "date": d, "moved": moved,
        "m_rev_old": m_rev_old, "m_rev_new": m_rev_new,
        "s_rev_old": s_rev_old, "s_rev_new": s_rev_new,
        "m_cogs_new": m_cogs_new, "s_cogs_new": s_cogs_new,
        "s_food_new": s_food_new, "s_bev_new": s_bev_new,
        "s_food_cogs": s_food_cogs, "s_bev_cogs": s_bev_cogs,
    })

    if not WRITE:
        continue

    # ---- Mari: all Kitchen, no bev slice (daily_aggregator's own convention) --
    m["revenue_ex_gst"] = f"{m_rev_new:.2f}"
    m["food_ex_gst"] = f"{m_rev_new:.2f}"
    m["bev_ex_gst"] = "0.0"
    m["cogs_dollars"] = f"{m_cogs_new:.2f}"
    m["food_cogs"] = f"{m_cogs_new:.2f}"
    m["bev_cogs"] = ""
    m["cogs_pct"] = _fmt(pct(m_cogs_new, m_rev_new))
    m["gp_dollars"] = f"{m_rev_new - m_cogs_new:.2f}"
    m["gp_pct"] = _fmt(pct(m_rev_new - m_cogs_new, m_rev_new))
    m["food_cogs_pct"] = _fmt(pct(m_cogs_new, m_rev_new))
    m["food_gp_pct"] = _fmt(pct(m_rev_new - m_cogs_new, m_rev_new))
    m["bev_cogs_pct"] = ""
    m["bev_gp_pct"] = ""

    # ---- Stow ---------------------------------------------------------------
    s["revenue_ex_gst"] = f"{s_rev_new:.2f}"
    s["food_ex_gst"] = f"{s_food_new:.2f}"
    s["bev_ex_gst"] = f"{s_bev_new:.2f}"
    s["cogs_dollars"] = f"{s_cogs_new:.2f}"
    s["food_cogs"] = f"{s_food_cogs:.2f}"
    s["bev_cogs"] = f"{s_bev_cogs:.2f}"
    s["cogs_pct"] = _fmt(pct(s_cogs_new, s_rev_new))
    s["gp_dollars"] = f"{s_rev_new - s_cogs_new:.2f}"
    s["gp_pct"] = _fmt(pct(s_rev_new - s_cogs_new, s_rev_new))
    s["food_cogs_pct"] = _fmt(pct(s_food_cogs, s_food_new))
    s["bev_cogs_pct"] = _fmt(pct(s_bev_cogs, s_bev_new))
    s["food_gp_pct"] = _fmt(pct(s_food_new - s_food_cogs, s_food_new))
    s["bev_gp_pct"] = _fmt(pct(s_bev_new - s_bev_cogs, s_bev_new))

    # Wage %s are wages / revenue and revenue just moved. Wage DOLLARS are not
    # touched — Deputy decides those, not Lightspeed.
    _restate_wage_pcts(m, m_rev_new, m["food_ex_gst"], m["bev_ex_gst"])
    _restate_wage_pcts(s, s_rev_new, s_food_new, s_bev_new)


# --------------------------------------------------------------
# Report
# --------------------------------------------------------------
if skipped:
    print(f"!! SKIPPED {len(skipped)} day(s) — left exactly as they were:")
    for d, why, a, b in skipped:
        print(f"   {d}  {why}: mari_ls ${a:,.2f} vs stored pair ${b:,.2f}")
    print()

material = [c for c in changes if abs(c["moved"]) >= 1]
print(f"Days restated          : {len(changes)}")
print(f"  of which >= $1 move  : {len(material)}")
print(f"Mari revenue moved     : ${sum(c['moved'] for c in changes):+,.2f} net"
      f"  (${sum(abs(c['moved']) for c in changes):,.2f} gross)")
print(f"\nGROUP TOTAL CONSERVATION CHECK  <- this is the one that matters")
print(f"  Stow+Mari before : ${group_before:,.2f}")
print(f"  Stow+Mari after  : ${group_after:,.2f}")
print(f"  drift            : ${group_after - group_before:+,.2f}")

print(f"\n25 largest moves:")
print(f"{'date':11} {'mari_old':>10} {'mari_new':>10} {'move':>10} {'stow_old':>10} {'stow_new':>10}")
for c in sorted(changes, key=lambda c: -abs(c["moved"]))[:25]:
    print(f"{c['date']:11} {c['m_rev_old']:>10,.2f} {c['m_rev_new']:>10,.2f} "
          f"{c['moved']:>+10,.2f} {c['s_rev_old']:>10,.2f} {c['s_rev_new']:>10,.2f}")

# Yearly shape, so the size of the restatement is visible per period rather than
# as one number nobody can sanity-check.
by_month = defaultdict(float)
for c in changes:
    by_month[c["date"][:7]] += c["moved"]
print(f"\nNet Mari move by month:")
for mth in sorted(by_month):
    if abs(by_month[mth]) >= 1:
        print(f"  {mth}  ${by_month[mth]:>+10,.2f}")

if not WRITE:
    print(f"\nDRY RUN — nothing written. Re-run with --write to apply.")
    sys.exit(0)

# --------------------------------------------------------------
# Save (backup first — this rewrites 549 days of history)
# --------------------------------------------------------------
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
for name, rows, fields in (("mari", mari_rows, mari_fields), ("stow", stow_rows, stow_fields)):
    src = DATA / f"{name}_daily_history.csv"
    bak = DATA / f"{name}_daily_history.bak_{stamp}.csv"
    shutil.copy2(src, bak)
    with src.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {src.name}  (backup: {bak.name})")
