#!/usr/bin/env python3
"""Regenerate scripts/product_dept_map.json from a Lightspeed PRODUCT EXPORT.

    my.kounta.com -> Products -> export (cloud-down icon) -> Export
    python3 scripts/build_dept_map.py ~/Downloads/product-export_YYYY-MM-DD_HHMMSS.csv

WHY THIS EXISTS (2026-07-17)
  The map used to be built from the weekly report's reporting_group_mapping.csv
  — a HISTORICAL AGGREGATE of what had sold. Anything it had never seen fell
  through classify_product() to the 'b' FOH catch-all, and for a Marilyna's
  product that is a silent DOUBLE COUNT: her report bills it, Stow doesn't
  recognise it as 'm' so Stow never strips it, both venues keep it.

  Measured against Lightspeed's own reporting groups across 586 days:
  ~$12,300 of revenue sat in the wrong venue. Mari and Stow were exact mirror
  images — the group total was always right, the SPLIT was not.

  A product export is not an aggregate. It is the register itself: every
  product, with the ReportingGroup Lightspeed actually files it under. It knows
  about '$60 BANQUET' ("Marilyna's Pizza") and 'Online Surcharge'
  ("Add-ons - Pizza") — both of which the aggregate had never heard of, and both
  of which were being billed to the wrong venue.

  The REAL fix is a `Reporting Group Name` column on the Insights email export:
  classify_product() prefers it over any map (see daily_aggregator.py ~line 207)
  and the map stops being consulted at all. Until that lands, this keeps the map
  honest — and it is worth re-running whenever products change.
"""
import csv, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

if len(sys.argv) < 2:
    sys.exit(__doc__)
src = Path(sys.argv[1]).expanduser()
if not src.exists():
    sys.exit(f"no such export: {src}")

import importlib.util
spec = importlib.util.spec_from_file_location("agg_head", Path(__file__).parent / "daily_aggregator.py")
_txt = (Path(__file__).parent / "daily_aggregator.py").read_text()
ns = {"__file__": str(Path(__file__).parent / "daily_aggregator.py"), "__name__": "agg_head"}
exec(compile(_txt.split("# ---- classify every row once ----")[0], "agg_head", "exec"), ns)
_rg_dept = ns["_rg_dept"]

rows = list(csv.DictReader(src.open()))
out = {"stow": {}, "hg": {}, "*": {}}
skipped = 0
for r in rows:
    name = (r.get("ProductName") or "").strip().strip('"')
    rg = (r.get("ReportingGroup") or "").strip().strip('"')
    if not name:
        continue
    if not rg:
        skipped += 1          # no RG in Lightspeed -> classify_product's 'b' catch-all
        continue
    for vkey, pfx in (("stowaway", "stow"), ("harry", "hg")):
        d = _rg_dept(rg, vkey)
        if d:
            out[pfx][name] = d

dst = Path(__file__).parent / "product_dept_map.json"
prev = json.loads(dst.read_text()) if dst.exists() else {"stow": {}, "hg": {}}

# MERGE, don't replace. A product export covers ONE Lightspeed site, and Harry
# Gatos is a different site — so HG's own products ("Harry's Lager",
# "Duck Spring Roll", "Chicken Karaage") are simply absent from Stowaway's
# export. Replacing outright dropped 792 products, 81 of which still sell, and
# would have dumped HG's entire food/bev split into the 'b' FOH catch-all.
#
# The export WINS wherever it knows a product — it is the live register, and
# that is the whole point. Anything it has never heard of keeps whatever the map
# already had: deleted products that still appear in historical exports, and
# every product belonging to another site.
merged = {"stow": dict(prev.get("stow", {})), "hg": dict(prev.get("hg", {}))}
for pfx in ("stow", "hg"):
    merged[pfx].update(out[pfx])
kept = len(set(merged["stow"]) - set(out["stow"]))

gen = (f"{src.name} — Lightspeed product export (the register), MERGED over the "
       f"previous map. {len(rows)} products, {len(out['stow'])} carry a reporting "
       f"group and win outright; {kept} entries kept from the old map because the "
       f"export has never heard of them (other sites' products, deleted lines). "
       f"Regenerate with scripts/build_dept_map.py; do NOT hand-edit.")
dst.write_text(json.dumps({"_generated": gen, "*": out["*"],
                           "stow": dict(sorted(merged["stow"].items())),
                           "hg": dict(sorted(merged["hg"].items()))}, indent=1) + "\n")
out = merged

print(f"products in export      : {len(rows)}")
print(f"  with a ReportingGroup : {len(rows) - skipped}")
print(f"  no RG -> 'b' catch-all: {skipped}")
print(f"\nmapped (stow): {len(prev.get('stow', {}))} -> {len(out['stow'])}")
added = set(out["stow"]) - set(prev.get("stow", {}))
moved = {p for p in set(out["stow"]) & set(prev.get("stow", {}))
         if out["stow"][p] != prev["stow"][p]}
print(f"  new           : {len(added)}")
print(f"  kept from old : {kept}   (export doesn't know them — other sites, deleted lines)")
print(f"  RECLASSIFIED  : {len(moved)}")
for p in sorted(moved)[:20]:
    print(f"     {p[:44]:46} {prev['stow'][p]} -> {out['stow'][p]}")
print(f"\n-> {dst}")
