#!/usr/bin/env python3
"""Combine the per-night EatClub JSONs (data/eatclub_hg_<date>.json) into a single
data/eatclub_nights.json that the EatClub dashboard fetches and renders live — so
the page stays current with each night instead of being a hand-built static snapshot.

Each nightly file: {date, venue, tables, covers, menu_inc, net_inc, giveaway_inc,
discount_inc, commission_inc}. giveaway_inc = discount_inc + commission_inc = the
channel cost; net_inc = menu_inc - giveaway_inc. All values inc-GST.

Run: python3 scripts/build_eatclub_nights.py   (idempotent; rebuilds the whole file)
"""
import glob, json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
COGS_PCT = 27.6   # HG actual COGS (trailing 4wk Xero purchases, per Zak 13 Jul); charged on full menu volume

def main():
    nights = []
    for path in sorted(glob.glob(os.path.join(DATA, "eatclub_hg_*.csv")) +
                       glob.glob(os.path.join(DATA, "eatclub_hg_*.json"))):
        m = re.search(r"eatclub_hg_(\d{4}-\d{2}-\d{2})\.(json|csv)$", os.path.basename(path))
        if not m:
            continue
        try:
            d = json.load(open(path))
        except Exception:
            continue
        if not isinstance(d, dict) or not d.get("date"):
            continue
        nights.append({
            "date": d["date"],
            "tables": d.get("tables", 0),
            "covers": d.get("covers", 0),
            "menu_inc": round(float(d.get("menu_inc") or 0), 2),
            "net_inc": round(float(d.get("net_inc") or 0), 2),
            "discount_inc": round(float(d.get("discount_inc") or 0), 2),
            "commission_inc": round(float(d.get("commission_inc") or 0), 2),
            "giveaway_inc": round(float(d.get("giveaway_inc")
                                    or (float(d.get("discount_inc") or 0) + float(d.get("commission_inc") or 0))), 2),
        })
    # dedupe by date (last wins), sort ascending
    by_date = {n["date"]: n for n in nights}
    nights = [by_date[k] for k in sorted(by_date)]

    out = {
        "venue": "Harry Gatos",
        "launch": "2026-07-01",
        "cogs_pct": COGS_PCT,
        "latest": nights[-1]["date"] if nights else None,
        "nights": nights,
    }
    with open(os.path.join(DATA, "eatclub_nights.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))
    tot_t = sum(n["tables"] for n in nights)
    tot_c = sum(n["covers"] for n in nights)
    print(f"eatclub_nights.json: {len(nights)} nights ({out['launch']} -> {out['latest']}), "
          f"{tot_t} tables, {tot_c} covers")


if __name__ == "__main__":
    main()
