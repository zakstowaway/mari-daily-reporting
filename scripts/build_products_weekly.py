#!/usr/bin/env python3
"""Build data/products_weekly.csv — per-product weekly ex-GST sales with the
reporting-group NAME, for the Menu Trends "Products" view.

Source: the committed Sales-by-Product exports (data/insights_<prefix>_<date>.csv).
Those are the full-site till dumps, so Stow's file also carries Marilyna's items
('m') and Harry Gatos food ('hgf'), and HG's file carries Stow food ('stf'). We
reattribute each product to its real venue with scripts/product_dept_map.json (the
exact same dept codes the daily aggregator uses), skip the Mari file entirely
(Mari's revenue IS the 'm' slice of the Stow till — reading both would double it),
and join the human reporting-group name from the Lightspeed product exports in
data/bo_exports/. Weeks end Sunday, matching data/rg_weekly.csv.

Output columns: week_ending,venue,reporting_group,product_name,sales_ex_gst,qty
Run: python3 scripts/build_products_weekly.py   (idempotent; rebuilds the whole file)
"""
import csv, glob, json, os, re, sys
from collections import defaultdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DEPT_MAP_FILE = os.path.join(ROOT, "scripts", "product_dept_map.json")
PRODUCT_OVERRIDES = {"$60 BANQUET": "m"}          # mirrors daily_aggregator
VKEY = {"stow": "stow", "hg": "hg"}               # dept-map sub-keys
# dept code -> the venue that revenue belongs to. f/b stay on the till's own venue.
DEPT_VENUE = {"m": "mari", "hgf": "hg", "stf": "stow"}
# friendly fallback group when a product carries no reporting group in Lightspeed.
UNMAPPED = {"f": "Kitchen (no reporting group)", "b": "Bar / FOH (no reporting group)",
            "m": "Marilyna's", "hgf": "Harry Gatos Food", "stf": "Stowaway Food"}


def parse_num(x):
    s = str(x or "").strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "").replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def week_ending(d):                                # Sunday of d's Mon-Sun week
    return d + timedelta(days=(6 - d.weekday()))


def load_dept_map():
    with open(DEPT_MAP_FILE) as f:
        return json.load(f)


def dept_for(name, prefix, dmap):
    n = (name or "").strip()
    if prefix == "mari":
        return "f"
    if n in PRODUCT_OVERRIDES:
        return PRODUCT_OVERRIDES[n]
    vk = VKEY.get(prefix)
    return (dmap.get(vk, {}).get(n) or dmap.get("*", {}).get(n) or "b")


def load_rg_names():
    """source-till prefix -> { product_name -> reporting_group_name }."""
    out = {"stow": {}, "hg": {}}
    for prefix, fn in (("stow", "stowaway_products.csv"), ("hg", "harry_gatos_products.csv")):
        path = os.path.join(DATA, "bo_exports", fn)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = (row.get("ProductName") or "").strip()
                g = (row.get("ReportingGroup") or "").strip()
                g = re.sub(r"\s*\[harrys\]\s*$", "", g, flags=re.I).strip()   # drop HG suffix
                if name and g:
                    out[prefix][name] = g
    return out


def main():
    dmap = load_dept_map()
    rgnames = load_rg_names()
    agg = defaultdict(lambda: [0.0, 0.0])   # (we, venue, rg, product) -> [ex_gst, qty]

    # Stow + HG till files only. Mari's revenue rides in on the Stow 'm' slice.
    for path in sorted(glob.glob(os.path.join(DATA, "insights_*.csv"))):
        m = re.match(r"insights_(stow|hg)_(\d{4}-\d{2}-\d{2})\.csv$", os.path.basename(path))
        if not m:
            continue
        prefix, dstr = m.group(1), m.group(2)
        we = week_ending(date.fromisoformat(dstr)).isoformat()
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = (row.get("Product Name") or row.get("ProductName") or "").strip()
                if not name:
                    continue                         # footer / subtotal row
                inc = parse_num(row.get("$ Sales") or row.get("Sales"))
                tax = parse_num(row.get("Total Tax"))
                ex = (inc - tax) if tax else inc / 1.1
                qty = parse_num(row.get("Product Quantity") or row.get("Qty") or row.get("Quantity"))
                if not ex:
                    continue
                code = dept_for(name, prefix, dmap)
                venue = DEPT_VENUE.get(code, prefix)             # reattribute cross-till
                rg = rgnames.get(prefix, {}).get(name) or UNMAPPED.get(code, "Unmapped")
                k = (we, venue, rg, name)
                agg[k][0] += ex
                agg[k][1] += qty

    rows = [{"week_ending": we, "venue": v, "reporting_group": rg, "product_name": p,
             "sales_ex_gst": round(a[0], 2), "qty": round(a[1], 2)}
            for (we, v, rg, p), a in agg.items()]
    rows.sort(key=lambda r: (r["week_ending"], r["venue"], r["reporting_group"], -r["sales_ex_gst"]))

    out = os.path.join(DATA, "products_weekly.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["week_ending", "venue", "reporting_group", "product_name", "sales_ex_gst", "qty"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    # reconciliation: per venue/week product totals (for a sanity eyeball)
    tot = defaultdict(float)
    for r in rows:
        tot[(r["week_ending"], r["venue"])] += r["sales_ex_gst"]
    print(f"products_weekly.csv: {len(rows)} rows, "
          f"{len({r['week_ending'] for r in rows})} weeks, "
          f"{len({r['product_name'] for r in rows})} products")
    for k in sorted(tot)[-9:]:
        print(f"  {k[0]} {k[1]:5} ex-GST ${tot[k]:,.0f}")


if __name__ == "__main__":
    main()
