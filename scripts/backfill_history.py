"""
Backfill the dashboard daily-history CSVs from the weekly-report masters.

Sources (canonical, same as the weekly Tuesday report):
  - Daily Sales/Stowaway.csv + HarryGatos.csv   (product masters, per-day rows
    with ReportingGroup — the same files build_rich_rollups.py reads)
  - Daily Sales/.drive-staging/wages_weekly.csv (per-dept weekly wages INC
    super, salaried handled — output of build_wages_from_deputy.py)

Reallocation rules (identical to build_rich_rollups.py):
  1. Marilynas carve-out: Stowaway rows with a MARILYNAS_RGS ReportingGroup
     are Marilynas venue revenue, never Stowaway.
  2. HG Food reallocation: Stowaway rows with RG 'Harry Gatos Food' are
     HarryGatos revenue (Kitchen/food slice), regardless of day.

Wages: weekly dept totals (TotalWagesIncSuper) are distributed across the
week's trading days proportional to that day's dept revenue slice
(Kitchen -> food rev, FOH -> bev rev, Mari Kitchen/Driver -> venue rev).
Property: within a week, each dept's daily wage%% equals the weekly dept
wage%% from wages_weekly.csv, so the dashboard reconciles with the Tuesday
report to the cent. Historical Stowaway 'Bar' dept rows are treated as FOH
(canon: query-layer fold, do not rename the CSV).

Rows for dates NEWER than the masters (live daily-pipeline rows) are kept
as-is; overlapping dates are replaced by the master-derived row (masters are
authoritative — e.g. the pre-footer-fix inflated Mari rows get corrected).

Usage:
  python3 backfill_history.py --daily-dir "<path to Daily Sales>" \
      --data-dir <repo data dir> [--from 2024-10-01] [--to 2026-07-05]
"""
import argparse, csv, json, sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

# ---- canonical RG sets (LIVE pipeline: Daily Sales/skill-patches/weekly-report/
# scripts/build_rich_rollups.py — NOT the stale packaged skill). Keep in sync. ----
MARILYNAS_RGS = {
    "marilyna's pizza", "marilynas pizza",
    "marilyna's soft drinks", "marilynas soft drinks",
    "add-ons - pizza",
    "dine-in pizza",
    "delivery alcohol", "delivery cocktails",
    # 'Delivery Kitchen' REMOVED 2026-05-13 per Zak: it IS Stow Kitchen food
    # on Uber — revenue and labour belong with Stow Kitchen.
}
HG_FOOD_RG = "harry gatos food"
STOW_FOOD_RG = "stow food"   # symmetric realloc (added 2026-05-13): HG row -> Stowaway

# Kitchen slice = canonical dept-takings table (SKILL.md) = live
# classify_rg_to_dept STOW_KITCHEN_RGS/HG_KITCHEN_RGS + Desserts +
# Delivery Kitchen (2026-05-13: Stow Kitchen food on Uber).
# FOH/bev is the CATCH-ALL, exactly like the live weekly classifier —
# anything not Kitchen (incl. Unmapped/Modifiers) lands in FOH.
FOOD = {'big plates','small plates','kitchen specials','salads','desserts','kids meals','kids',
        'add-ons - kitchen','harry gatos food','stow food','delivery kitchen',
        'sides','mains','snacks','yum cha','staff dinners'}


def norm_rg(rg: str) -> str:
    k = (rg or '').strip().lower()
    if k.endswith(' [harrys]'):
        k = k[:-len(' [harrys]')]
    return k


def route(src_venue: str, rg: str):
    """-> (venue_key, dept) with venue_key in mari/stow/hg, dept f/b/o."""
    k = norm_rg(rg)
    if src_venue == 'Stowaway':
        if k in MARILYNAS_RGS:
            return 'mari', 'f'
        if k == HG_FOOD_RG:
            return 'hg', 'f'
        v = 'stow'
    else:
        if k == STOW_FOOD_RG:
            return 'stow', 'f'
        v = 'hg'
    return v, ('f' if k in FOOD else 'b')


def status(v, c):
    if v is None or not c:
        return "unknown"
    if v >= c.get("red", float("inf")):   return "red"
    if v >= c.get("amber", float("inf")): return "amber"
    if v <= c.get("target", float("inf")): return "green"
    return "yellow"


FIELDS = ["date","revenue_ex_gst","cogs_dollars","cogs_pct","wages_dollars","wages_pct",
          "delivery_dollars","delivery_pct","gp_dollars","gp_pct",
          "cogs_alert","wages_alert","delivery_alert","gp_alert",
          "food_ex_gst","bev_ex_gst","food_cogs","bev_cogs",
          "food_cogs_pct","bev_cogs_pct","food_gp_pct","bev_gp_pct",
          "wages_kitchen_dollars","wages_foh_dollars","wages_kitchen_pct","wages_foh_pct",
          "cogs_food_alert","cogs_bev_alert","wages_kitchen_alert","wages_foh_alert",
          "uber_eats_revenue","uber_direct_dollars","leave_dollars"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-dir", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--baselines-dir", default=None)
    ap.add_argument("--from", dest="dfrom", default="2000-01-01")
    ap.add_argument("--to", dest="dto", default="2100-01-01")
    a = ap.parse_args()
    daily = Path(a.daily_dir); data = Path(a.data_dir)
    bdir = Path(a.baselines_dir) if a.baselines_dir else data.parent / "baselines"

    # ---- aggregate masters per (venue, date) ----
    agg = defaultdict(lambda: {"rev": 0.0, "cogs": 0.0,
                               "f_rev": 0.0, "f_cogs": 0.0,
                               "b_rev": 0.0, "b_cogs": 0.0,
                               "o_rev": 0.0})
    for fname in ("Stowaway.csv", "HarryGatos.csv"):
        with (daily / fname).open() as fh:
            for r in csv.DictReader(fh):
                d = r["Date"]
                if not (a.dfrom <= d <= a.dto):
                    continue
                v, dept = route(r["Venue"], r.get("ReportingGroup", ""))
                amt = float(r["Sale Amount"] or 0)
                cost = float(r["Cost"] or 0)
                cell = agg[(v, d)]
                cell["rev"] += amt
                cell["cogs"] += cost
                if dept == 'f':
                    cell["f_rev"] += amt; cell["f_cogs"] += cost
                elif dept == 'b':
                    cell["b_rev"] += amt; cell["b_cogs"] += cost
                else:
                    cell["o_rev"] += amt

    # ---- weekly wages -> daily spread ----
    # wages[(venue, week_ending)][dept] = TotalWagesIncSuper
    wages = defaultdict(dict)
    ww = daily / ".drive-staging" / "wages_weekly.csv"
    if ww.exists():
        with ww.open() as fh:
            for r in csv.DictReader(fh):
                ven = {"Stowaway": "stow", "HarryGatos": "hg", "Marilynas": "mari"}.get(r["Venue"])
                if ven is None:
                    # Group/Leave (weekly canon) rides on the stow rows as a
                    # separate Leave bucket — group-level overhead, excluded
                    # from venue wage totals, added to Group wages client-side.
                    if r["Venue"] == "Group" and r["Department"] == "Leave":
                        ven = "stow"
                    else:
                        continue
                dept = r["Department"]
                if dept in ("Bar", "Floor"):
                    dept = "FOH"          # canon: historical fold at query layer
                if dept == "Venue Total":
                    dept = "_TOTAL"       # kept to derive the venue-level residual
                elif dept not in ("FOH", "Kitchen", "Driver", "Leave"):
                    continue
                w = float(r["TotalWagesIncSuper"] or 0)
                wages[(ven, r["WeekEnding"])][dept] = wages[(ven, r["WeekEnding"])].get(dept, 0.0) + w

    # Venue Total exceeds dept sum (admin 90/10 split, salaried residuals) —
    # spread that residual across days by venue revenue so weekly totals
    # reconcile with wages_weekly to the cent.
    for key, depts in wages.items():
        tot = depts.pop("_TOTAL", None)
        if tot is not None:
            resid = tot - sum(v for k, v in depts.items() if k != "Leave")
            if abs(resid) > 0.01:
                depts["Residual"] = resid

    def week_days(week_end: str):
        we = date.fromisoformat(week_end)
        return [(we - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]

    # spread: wages_day[(venue, date)][dept]
    wages_day = defaultdict(dict)
    for (ven, we), depts in wages.items():
        days = week_days(we)
        for dept, wtot in depts.items():
            if not wtot:
                continue
            def slice_rev(dstr):
                c = agg.get((ven, dstr))
                if not c:
                    return 0.0
                if ven == "mari":
                    return c["rev"]
                return c["f_rev"] if dept == "Kitchen" else c["b_rev"] if dept == "FOH" else c["rev"]
            weights = {dstr: slice_rev(dstr) for dstr in days}
            tot = sum(weights.values())
            if tot <= 0:
                open_days = [dstr for dstr in days if agg.get((ven, dstr))]
                if not open_days:
                    continue
                weights = {dstr: 1.0 for dstr in open_days}; tot = len(open_days)
            for dstr, wgt in weights.items():
                if wgt <= 0:
                    continue
                wages_day[(ven, dstr)][dept] = wages_day[(ven, dstr)].get(dept, 0.0) + wtot * wgt / tot

    # ---- baselines ----
    targets = {}
    for ven, bf in (("mari", "mari_baseline.json"), ("stow", "stow_baseline.json"), ("hg", "hg_baseline.json")):
        p = bdir / bf
        targets[ven] = json.loads(p.read_text())["targets_and_alerts"] if p.exists() else {}

    # ---- build rows ----
    out = defaultdict(dict)   # venue -> date -> row
    for (ven, dstr), c in agg.items():
        if c["rev"] == 0 and c["cogs"] == 0:
            continue
        t = targets[ven]
        rev_ex = c["rev"] / 1.1
        cogs = c["cogs"]
        gp = rev_ex - cogs
        cogs_pct = cogs / rev_ex * 100 if rev_ex else None
        gp_pct = gp / rev_ex * 100 if rev_ex else None
        wd = wages_day.get((ven, dstr), {})
        kit = wd.get("Kitchen"); foh = wd.get("FOH"); drv = wd.get("Driver")
        resid = wd.get("Residual")
        leave = wd.get("Leave")
        wages_tot = None
        if kit is not None or foh is not None or resid is not None:
            wages_tot = (kit or 0) + (foh or 0) + (resid or 0)
        wages_pct = wages_tot / rev_ex * 100 if (wages_tot is not None and rev_ex) else None
        split = ven in ("stow", "hg")
        f_ex = c["f_rev"] / 1.1; b_ex = c["b_rev"] / 1.1
        fc = c["f_cogs"]; bc = c["b_cogs"]
        wk_pct = (kit / f_ex * 100) if (split and kit is not None and f_ex) else None
        wf_pct = (foh / b_ex * 100) if (split and foh is not None and b_ex) else None
        delivery = drv if ven == "mari" else None
        delivery_pct = delivery / rev_ex * 100 if (delivery is not None and rev_ex) else None
        fmt = lambda x, n=2: (round(x, n) if x is not None else "")
        row = {
            "date": dstr,
            "revenue_ex_gst": fmt(rev_ex),
            "cogs_dollars": fmt(cogs),
            "cogs_pct": fmt(cogs_pct, 1),
            "wages_dollars": fmt(wages_tot),
            "wages_pct": fmt(wages_pct, 1),
            "delivery_dollars": fmt(delivery),
            "delivery_pct": fmt(delivery_pct, 1),
            "gp_dollars": fmt(gp),
            "gp_pct": fmt(gp_pct, 1),
            "cogs_alert": status(cogs_pct, t.get("cogs")),
            "wages_alert": status(wages_pct, t.get("wages")),
            "delivery_alert": status(delivery_pct, t.get("delivery")) if ven == "mari" else "n/a",
            "gp_alert": "unknown",
            "food_ex_gst": fmt(f_ex) if split else "",
            "bev_ex_gst": fmt(b_ex) if split else "",
            "food_cogs": fmt(fc) if split else "",
            "bev_cogs": fmt(bc) if split else "",
            "food_cogs_pct": fmt(fc / f_ex * 100, 1) if split and f_ex else "",
            "bev_cogs_pct": fmt(bc / b_ex * 100, 1) if split and b_ex else "",
            "food_gp_pct": fmt((f_ex - fc) / f_ex * 100, 1) if split and f_ex else "",
            "bev_gp_pct": fmt((b_ex - bc) / b_ex * 100, 1) if split and b_ex else "",
            "wages_kitchen_dollars": fmt(kit) if split and kit is not None else "",
            "wages_foh_dollars": fmt(foh) if split and foh is not None else "",
            "wages_kitchen_pct": fmt(wk_pct, 1) if wk_pct is not None else "",
            "wages_foh_pct": fmt(wf_pct, 1) if wf_pct is not None else "",
            "cogs_food_alert": status(fc / f_ex * 100 if split and f_ex else None, t.get("cogs_food")),
            "cogs_bev_alert": status(bc / b_ex * 100 if split and b_ex else None, t.get("cogs_bev")),
            "wages_kitchen_alert": status(wk_pct, t.get("wages_kitchen")),
            "wages_foh_alert": status(wf_pct, t.get("wages_foh")),
            "uber_eats_revenue": 0,
            "uber_direct_dollars": "",
            "leave_dollars": fmt(leave) if leave is not None else "",
        }
        out[ven][dstr] = row

    # ---- merge with existing pipeline rows (keep dates newer than masters) ----
    for ven, prefix in (("mari", "mari"), ("stow", "stow"), ("hg", "hg")):
        hist = data / f"{prefix}_daily_history.csv"
        merged = dict(out[ven])
        max_master = max(merged) if merged else "1900-01-01"
        kept = 0
        if hist.exists():
            with hist.open() as fh:
                for r in csv.DictReader(fh):
                    if r["date"] > max_master and r["date"] not in merged:
                        merged[r["date"]] = {k: r.get(k, "") for k in FIELDS}
                        kept += 1
        rows = [merged[d] for d in sorted(merged)]
        with hist.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"{prefix}: {len(rows)} rows ({min(merged)} -> {max(merged)}), "
              f"{kept} live pipeline rows kept beyond master range")


if __name__ == "__main__":
    main()
