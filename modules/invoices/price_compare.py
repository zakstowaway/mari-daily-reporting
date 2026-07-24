"""
Cross-supplier price comparison — the "is Foodlink chicken cheaper than B&E?" view.

Every cogs row already reduces to a canonical unit cost ($/kg, $/L, $/each) via
pack_size.parse_pack. To line suppliers up we must first decide when two
differently-worded lines are the SAME ingredient — "CARROT KG" (Select Fresh)
and "Carrots Loose" (Fresh Fruit Team). Two layers do it:

  1. AUTO — normalise the description to a canonical key: strip pack sizes,
     packaging words and grade/qualifier noise, singularise, then keep the
     significant tokens SORTED so word order doesn't matter. This UNDER-merges
     rather than over-merges: it only groups lines that share their core words,
     never lumping unrelated things together. Missing a synonym is safe; a wrong
     merge silently corrupts a price comparison, which is worse.

  2. MANUAL — data/ingredient_aliases.json maps one canonical key onto another,
     so a human can say "these two ARE the same" once and it sticks. Same
     correction-that-teaches pattern the coding loop uses.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALIASES = ROOT / "data" / "ingredient_aliases.json"

# Words that describe the PACK or the GRADE, not the ingredient. Dropped from the
# identity key so "OLIVES 2KG" and "Olives (bulk tub)" land together.
_NOISE = {
    # packaging / unit of measure
    "bag", "bags", "ctn", "carton", "cartons", "box", "boxes", "punnet", "punnets",
    "tray", "trays", "each", "ea", "bch", "bunch", "bunches", "pkt", "pkts", "pack",
    "packs", "packet", "tub", "tubs", "jar", "jars", "btl", "bottle", "bottles",
    "can", "cans", "tin", "tins", "case", "cases", "dz", "doz", "dozen", "loose",
    "per", "pc", "pcs", "piece", "pieces", "portion", "portions", "sleeve", "bulk",
    "pkg", "cup", "cups", "bottled", "canned", "jarred",
    # grade / state noise that isn't the ingredient's identity
    "imp", "imported", "premium", "prem", "fresh", "frozen", "fzn", "chilled",
    "rw", "avg", "approx", "cryo", "cryovac", "select", "grade", "quality",
    "nd", "nds", "seconds", "class", "no", "brand", "product", "assorted",
}
# units already captured by pack_size — never part of identity
_UNITS = {"kg", "kgs", "g", "gm", "gms", "gram", "grams", "ml", "l", "lt", "ltr",
          "litre", "litres", "liter", "liters", "kilo", "kilos"}

_NUM = re.compile(r"^\d+(\.\d+)?$")
_PACKNUM = re.compile(r"\b\d+(\.\d+)?\s*(kg|kgs|g|gm|gms|gram|grams|ml|l|lt|ltr|litres?|liters?|kilos?|x|inch|in|mm|cm)\b", re.I)
_PARENS = re.compile(r"\([^)]*\)")


def _singular(t: str) -> str:
    if len(t) <= 3:
        return t
    if t.endswith("oes"):
        return t[:-2]           # tomatoes -> tomato, potatoes -> potato
    if t.endswith("ies"):
        return t[:-3] + "y"     # berries -> berry
    if t.endswith("ss"):
        return t                # class, glass — leave
    if t.endswith("s"):
        return t[:-1]           # carrots -> carrot
    return t


def tokens(description: str) -> list[str]:
    """Significant identity tokens, original order, noise/pack/units removed."""
    d = _PARENS.sub(" ", (description or "").lower())
    d = _PACKNUM.sub(" ", d)                      # "5kg", "200g", "6x" -> gone
    d = re.sub(r"[^a-z0-9]+", " ", d)             # punctuation/dashes -> space
    out = []
    for raw in d.split():
        if _NUM.match(raw):                       # bare number
            continue
        if raw in _UNITS or raw in _NOISE:
            continue
        t = _singular(raw)
        if t and t not in _NOISE and len(t) > 1:
            out.append(t)
    return out


def _load_aliases() -> dict[str, str]:
    if not ALIASES.exists():
        return {}
    try:
        d = json.loads(ALIASES.read_text())
        return {k: v for k, v in (d.get("merge", d)).items()} if isinstance(d, dict) else {}
    except Exception:
        return {}


def canonical_key(description: str, aliases: dict[str, str] | None = None) -> str:
    """Identity key: sorted significant tokens, then any manual alias applied."""
    key = " ".join(sorted(tokens(description)))
    aliases = _load_aliases() if aliases is None else aliases
    # follow an alias chain (a->b->c) but never loop
    seen = set()
    while key in aliases and key not in seen:
        seen.add(key)
        key = aliases[key]
    return key


def display_name(description: str) -> str:
    """Human label built from the identity tokens in their original order."""
    ts = tokens(description)
    return " ".join(w.capitalize() for w in ts) if ts else (description or "").strip().title()
