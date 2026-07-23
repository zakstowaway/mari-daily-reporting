#!/usr/bin/env python3
"""
Build data/ingredients.json -- the chef-facing ingredient list.

    python3 scripts/build_ingredients.py

THE POINT
---------
The ingredient list is DERIVED FROM WHAT YOU ACTUALLY BOUGHT. It is not a
database anyone maintains. Buy something -> it appears. Stop buying it -> it
ages out. New supplier, new product, no admin.

This is the thing Lightspeed cannot do. Its product DB is hand-curated, which
is why SKU is populated on 3.9% of Stowaway / 5.4% of HG, why HG liquor is
0/144, and why the food menu -- the part that changed supplier -- has no
recipes and reports $0.00 cost on 4.6% of revenue.

THE CONVERSION THAT MATTERS
---------------------------
An invoice says   "SQUID PINEAPPLE CUT IMP U5 5KG"  $57.00
A chef thinks     "200g per serve"

So every ingredient needs a cost in a unit a chef will actually type. That
means parsing the pack out of the description:

    SQUID ... 5KG              -> 5 kg      -> $11.40/kg  -> $0.0114/g
    CHEESE CAMEMBERT 125GM     -> 125 g     -> $0.3648/g
    CORN CHIPS ... 6X500GM     -> 3000 g    -> $0.0158/g
    FLOUR TORTILLAS 12X63GM    -> 756 g     -> ...

WHERE THE PACK CANNOT BE PARSED WE DO NOT GUESS. The ingredient still ships,
flagged `needs_pack_review`, and the UI asks the chef to state the pack once.
A guessed pack size silently scales every recipe that uses it -- the same
class of error as a case total in a per-unit field, which is what
scripts/invoices/ exists to stop. Fail toward asking.

Traps encoded from real descriptions:
    "10INCH"  is not a pack     "U5" is a grade, not a count
    "200/300" is a size grade   "TRI" is a shape
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COGS = ROOT / "data" / "cogs_list.csv"
OUT = ROOT / "data" / "ingredients.json"

# Suppliers whose goods a chef cooks with. Liquor is a different UI problem
# (a bottle IS the unit); keep this list explicit rather than clever.
KITCHEN_SUPPLIERS = {
    "Select Fresh", "B&E", "Foodlink", "Gulli", "Sun Circle",
    "Fresh Fruit Team", "FFT", "Andrews Meat", "Jun Pacific",
}

RECENT_DAYS = 90

# --- pack parsing ----------------------------------------------------------
# Order matters: multipack before single, or "6X500GM" reads as "500GM".
_MULTI = re.compile(r"(?<![\d/])(\d{1,3})\s*[xX]\s*(\d+(?:\.\d+)?)\s*(KG|GM|G|ML|LT|L)\b", re.I)
_SINGLE = re.compile(r"(?<![\d/xX])(\d+(?:\.\d+)?)\s*(KG|GM|G|ML|LT|L)\b", re.I)

_TO_BASE = {  # -> (grams|ml), base unit
    "KG": (1000, "g"), "GM": (1, "g"), "G": (1, "g"),
    "L": (1000, "ml"), "LT": (1000, "ml"), "ML": (1, "ml"),
}

# Things that look like packs but are not. All from real invoice text.
_NOT_A_PACK = re.compile(r"\b(\d+\s*INCH|U\d+|\d+/\d+)\b", re.I)

# --- sanity bounds ---------------------------------------------------------
# A parsed pack can be arithmetically perfect and still 30x wrong, because
# descriptions state the PIECE size while the price is for the CASE. Caught
# on the very first run:
#
#     "CHEESE CAMEMBERT 125GM Rosenberg"  $45.60
#       -> parsed 125g -> $0.3648/g -> $364/kg
#
# Camembert is not $364/kg. The 125g is one wheel; $45.60 buys a box of them.
# This is the ILG/Paramount unit-cost trap in a chef's hat, and it matters:
# Baked Camembert is one of the 11 zero-cost products. Shipping this would
# move it from $0.00/serve (100% GP) to ~$45/serve (negative GP).
#
# Bounds are deliberately WIDE -- a smoke alarm, not a thermostat. Anything
# outside goes to the chef to state the pack, which is 30 seconds and correct,
# rather than into a recipe, which is silent and wrong for a month.
_BOUNDS = {
    #            min $/unit   max $/unit
    "g":        (Decimal("0.0005"), Decimal("0.20")),   # $0.50/kg .. $200/kg
    "ml":       (Decimal("0.0005"), Decimal("0.15")),   # $0.50/L  .. $150/L
    "bunch":    (Decimal("0.50"),   Decimal("30.00")),
    "tray":     (Decimal("2.00"),   Decimal("120.00")),
    "punnet":   (Decimal("1.00"),   Decimal("30.00")),
    "ea":       (Decimal("0.05"),   Decimal("100.00")),
    "doz":      (Decimal("2.00"),   Decimal("120.00")),
    "box":      (Decimal("2.00"),   Decimal("400.00")),
    "pkt":      (Decimal("0.50"),   Decimal("80.00")),
}


def out_of_bounds(cost_per_unit: Decimal, unit: str) -> str | None:
    b = _BOUNDS.get(unit)
    if not b:
        return None
    lo, hi = b
    if cost_per_unit < lo:
        return f"${cost_per_unit}/{unit} is implausibly CHEAP (< ${lo}) — pack likely overstated"
    if cost_per_unit > hi:
        return (f"${cost_per_unit}/{unit} is implausibly DEAR (> ${hi}) — the description "
                f"probably states the PIECE size while the price is for the CASE")
    return None


def parse_pack(desc: str) -> tuple[Decimal | None, str | None, str]:
    """
    -> (qty_in_base_units, base_unit, how)

    Returns (None, None, reason) when the pack is not confidently readable.
    That is a feature. See module docstring.
    """
    d = _NOT_A_PACK.sub(" ", desc)

    m = _MULTI.search(d)
    if m:
        count, size, unit = int(m.group(1)), Decimal(m.group(2)), m.group(3).upper()
        mult, base = _TO_BASE[unit]
        return Decimal(count) * size * mult, base, f"{count}x{size}{unit.lower()}"

    m = _SINGLE.search(d)
    if m:
        size, unit = Decimal(m.group(1)), m.group(2).upper()
        mult, base = _TO_BASE[unit]
        return size * mult, base, f"{size}{unit.lower()}"

    # A bunch / each / tray is a legitimate unit -- not a failure to parse.
    for word, unit in (("BCH", "bunch"), ("BUNCH", "bunch"), ("TRAY", "tray"),
                       ("PUNNET", "punnet"), ("EACH", "ea"), ("DOZ", "doz")):
        if re.search(rf"\b{word}\b", desc, re.I):
            return Decimal(1), unit, word.lower()

    # BARE UNIT = PRICED BY THAT UNIT. "ONION BROWN KG" is not a missing pack
    # size; it is how produce is sold -- $2.40 per kg, buy what you like.
    # Missed on the first run and it skipped half of Select Fresh (onion,
    # carrot, lemon, garlic), which is most of what a kitchen actually cooks.
    m = re.search(r"(?:^|\s)(?:/\s*)?(KG|LT|L|ML|GM|G)\s*$", desc, re.I)
    if m:
        u = m.group(1).upper()
        mult, base = _TO_BASE[u]
        return Decimal(mult), base, f"per {u.lower()}"

    return None, None, "no pack found in description"


# Discrete units an invoice may name in the description OR a note, when there is
# no weight to parse. "Celeriac ... Each", "Tomatoes Cherry ... Punnet".
_DISCRETE = [
    ("BUNCH", "bunch"), ("BCH", "bunch"), ("PUNNET", "punnet"), ("TRAY", "tray"),
    ("BOX", "box"), ("EACH", "ea"), ("DOZ", "doz"), ("PKT", "pkt"), ("PACKET", "pkt"),
]


def resolve_pack(desc: str, cost, basis: str = "", note: str = ""
                 ) -> tuple[Decimal | None, str | None, Decimal | None, str, str | None]:
    """
    THE one place a supplier line becomes a cost in a unit a chef can use.

    -> (qty_in_base_units, unit, cost_per_unit, how, review_reason|None)

    Uses the invoice's STRUCTURED fields, not just the free-text description —
    that is the fix for produce like "Cauliflower Florets" (no weight in the
    name, but the invoice says basis=per_kg) and "Celeriac … Each". Order:

      1. Liquor bases (per_bottle/keg/can): the unit IS the pack.
      2. Sold by weight/volume (per_kg / per_L): price already per kg/L. Cleanest.
      3. per_unit: read the pack weight from the description; a carton note
         (CTN-N) multiplies a single piece — this is what rescues the camembert
         ($45.60 is a box of 12 x 125g, not one 125g wheel).
      4. Still no weight: take a discrete unit the invoice names (Each/Punnet/
         Box/Bunch). Costable in that unit; the chef converts to grams once if
         they portion by weight.
      5. Genuinely unknown: ask, never guess.
    """
    cost = Decimal(str(cost))
    b = (basis or "").lower().replace("per_", "")
    note = note or ""

    if b in ("bottle", "keg", "can"):
        return Decimal(1), b, cost, b, out_of_bounds(cost, b)
    if b == "kg":
        return Decimal(1000), "g", (cost / 1000).quantize(Decimal("0.000001")), "per kg (invoice)", None
    if b in ("lt", "l", "litre"):
        return Decimal(1000), "ml", (cost / 1000).quantize(Decimal("0.000001")), "per L (invoice)", None

    qty, unit, how = parse_pack(desc)
    if qty and unit and unit in ("g", "ml"):
        ctn = re.search(r"CTN[-\s]?(\d+)", note, re.I)
        if ctn and "x" not in how:          # a single piece + "carton of N"
            n = int(ctn.group(1))
            qty, how = qty * n, f"{how} x CTN-{n} (invoice)"
        per = (cost / qty).quantize(Decimal("0.000001"))
        return qty, unit, per, how, out_of_bounds(per, unit)
    if qty and unit:                          # parse_pack already found a discrete unit
        return qty, unit, cost, how, out_of_bounds(cost, unit)

    hay = f"{desc} {note}"
    for word, u in _DISCRETE:
        if re.search(rf"\b{word}\b", hay, re.I):
            return Decimal(1), u, cost, f"per {u} (invoice)", out_of_bounds(cost, u)

    return None, None, None, how, "no pack size on the invoice — confirm once"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> int:
    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
    cutoff = date.today() - timedelta(days=RECENT_DAYS)

    out, review = [], 0
    seen: set[str] = set()
    for r in rows:
        if r["supplier"] not in KITCHEN_SUPPLIERS:
            continue
        try:
            seen_date = datetime.fromisoformat(r["invoice_date"]).date()
        except Exception:
            continue
        if seen_date < cutoff:
            continue

        desc = r["invoice_description"].strip()
        key = slug(f"{r['supplier']}-{r['supplier_code'] or desc}")
        if key in seen:
            continue
        seen.add(key)

        pack_cost = Decimal(r["cost_per_unit_incl_gst"])
        qty, unit, per, how, bad = resolve_pack(
            desc, pack_cost, basis=r.get("basis", ""), note=r.get("note", ""))

        item = {
            "id": key,
            "description": desc,           # verbatim -- chefs recognise supplier wording
            "supplier": r["supplier"],
            "supplier_code": r["supplier_code"] or None,
            "pack_cost_incl": str(pack_cost),
            "source_invoice": r["source_invoice"],
            "last_seen": r["invoice_date"],
            "venue": r["venue"],
        }
        if qty and unit:
            item["pack_qty"] = str(qty)
            item["pack_unit"] = unit
            item["pack_parsed_as"] = how
            item["cost_per_base_unit"] = str(per)   # the number the UI multiplies by
            item["needs_pack_review"] = bool(bad)
            if bad:
                item["review_reason"] = bad         # arithmetically fine, physically absurd
                review += 1
            else:
                item["needs_pack_review"] = False
        else:
            item["needs_pack_review"] = True
            item["review_reason"] = bad or how
            review += 1
        out.append(item)

    out.sort(key=lambda i: (i["needs_pack_review"], i["description"]))
    OUT.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": RECENT_DAYS,
        "source": "supplier invoices (scripts/invoices/) via data/cogs_list.csv",
        "note": "Derived from what was actually purchased. Nobody maintains this list.",
        "ingredients": out,
    }, indent=2))

    print(f"{len(out)} ingredients -> {OUT.relative_to(ROOT)}")
    print(f"  pack parsed:  {len(out)-review}")
    print(f"  needs review: {review}  (UI asks the chef; we do not guess)")
    print("\nsample:")
    for i in out[:8]:
        if i["needs_pack_review"]:
            print(f"  [review] {i['description'][:40]:<42} ${i['pack_cost_incl']:>8}  ({i['review_reason']})")
        else:
            print(f"  {i['description'][:40]:<42} ${i['cost_per_base_unit']}/{i['pack_unit']}"
                  f"   (pack {i['pack_parsed_as']} @ ${i['pack_cost_incl']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
