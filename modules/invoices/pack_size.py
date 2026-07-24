"""
Read the pack size off an invoice line so every cost can be reduced to a
canonical unit — $/kg, $/L or $/each — which is what the recipe builder needs and
what makes suppliers comparable (Foodlink chicken $/kg vs B&E chicken $/kg).

The size is almost always printed in the description: "SOUR CREAM 2LT",
"OLIVES 2KG", "Mushroom (200G Punnet)", "6x750ML". parse_pack returns how much of
a base unit sits in ONE purchase unit (the thing the line price is per), so:

    base_qty, base_unit = parse_pack("SOUR CREAM FULL 2LT")   # (2, "L")
    cost_per_L = line_unit_price / base_qty

Variable-weight items ("AVG 4.4KG R/W") are priced per actual kg — the parser
already reads the real line total, so pass is_weight_priced=True and we treat the
line as already per-kg rather than trusting the printed average.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

_MULTI = re.compile(r"(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(kg|gms?|gram?s?|g|ml|lt?r?|litres?|l)\b", re.I)
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|kilo(?:gram)?s?|gms?|gram?s?|g)\b", re.I)
_VOLUME = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|litres?|ltr?|l)\b", re.I)


def _kg(v: Decimal, unit: str) -> Decimal:
    u = unit.lower()
    return v / 1000 if u.startswith("g") else v          # g / gm / gram -> kg


def _litres(v: Decimal, unit: str) -> Decimal:
    return v / 1000 if unit.lower() == "ml" else v       # ml -> L


def _d(s: str) -> Optional[Decimal]:
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_pack(description: str, raw_uom: Optional[str] = None,
               is_weight_priced: bool = False) -> tuple[Decimal, str]:
    """(base_qty, base_unit) contained in one purchase unit. base_unit in kg|L|ea."""
    if is_weight_priced:               # priced per actual kg already
        return Decimal("1"), "kg"
    d = f"{description or ''} {raw_uom or ''}"

    m = _MULTI.search(d)               # "6x750ML", "24x330ML" -> whole outer
    if m:
        n, size, unit = _d(m.group(1)), _d(m.group(2)), m.group(3)
        if n and size:
            return ((_litres(n * size, unit), "L") if unit.lower().startswith(("ml", "l"))
                    else (_kg(n * size, unit), "kg"))

    m = _VOLUME.search(d)              # prefer volume/weight tokens with a number
    if m and _d(m.group(1)):
        return _litres(_d(m.group(1)), m.group(2)), "L"
    m = _WEIGHT.search(d)
    if m and _d(m.group(1)):
        return _kg(_d(m.group(1)), m.group(2)), "kg"

    return Decimal("1"), "ea"          # sold by the each/bunch/punnet/tray
