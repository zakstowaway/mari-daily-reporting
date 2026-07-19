"""
Prep labour — from the timer, per person, at that person's real rate.

Zak (2026-07-19): "the wages need to come from the prep timer, which will be
recorded per-user ... Marssheel will record his prep times, and you can use his
exact rates for prep sessions he submits."

So labour is NOT a team average applied to a nominal prep time. It is a set of
PREP SESSIONS — each one a real person, a real number of minutes, on a real day
— and each session is costed at THAT person's exact effective rate. A dish's
prep-labour cost is the average of what it has actually taken, in dollars, using
the recorders' own rates. Different hands cost different money; this keeps that.

WHAT A MINUTE OF SOMEONE'S TIME COSTS
-------------------------------------
    base hourly  x  (1 + super + on-costs)

    base hourly  their exact rate from wage_calibration.json (per employee id)
    super        superannuation guarantee (employer-paid, on top of the wage)
    on-costs     payroll tax + workers-comp, the actual figure the P&L uses

IDENTITY
--------
Prep is recorded against the logged-in person. We resolve them to a Deputy
employee (data/employee_map.json: id -> name) and their rate
(data/wage_calibration.json: id -> rate_per_hour). A session may carry the
employee_id directly (best) or just a name we match. No rate on file -> the
session's cost is unknown and is surfaced as such, never quietly zeroed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
CALIB = ROOT / "data" / "wage_calibration.json"
ONCOSTS = ROOT / "data" / "wage_oncosts.json"
EMP_MAP = ROOT / "data" / "employee_map.json"       # id -> name

# Superannuation guarantee, 2025-26. Employer cost on top of the wage.
SUPER_RATE = Decimal("0.115")


# ------------------------------------------------------------------ rates ----

def _oncost_rate() -> Decimal:
    if not ONCOSTS.exists():
        return Decimal("0")
    return Decimal(str(json.loads(ONCOSTS.read_text()).get("oncost_rate", 0)))


def _effective_multiplier() -> Decimal:
    return Decimal("1") + SUPER_RATE + _oncost_rate()


def _calib() -> dict:
    return json.loads(CALIB.read_text()) if CALIB.exists() else {}


def _resolve_id(ref: str) -> Optional[str]:
    """Accept an employee id or a name; return the employee id."""
    ref = str(ref).strip()
    calib = _calib()
    if ref in calib:                       # already an id
        return ref
    names = json.loads(EMP_MAP.read_text()) if EMP_MAP.exists() else {}
    low = ref.lower()
    # id->name in EMP_MAP, and name lives in calib too; match either
    for eid, name in names.items():
        if str(name).strip().lower() == low:
            return eid
    for eid, e in calib.items():
        if str(e.get("name", "")).strip().lower() == low:
            return eid
    return None


def rate_per_minute_for(ref: str) -> Optional[Decimal]:
    """
    Effective employer cost of one minute of THIS person's time. None if we
    have no rate on file for them — the caller must handle "unknown", not guess.
    """
    eid = _resolve_id(ref)
    if not eid:
        return None
    e = _calib().get(eid) or {}
    base = e.get("rate_per_hour")
    if base is None:
        return None
    effective_hour = Decimal(str(base)) * _effective_multiplier()
    return (effective_hour / 60).quantize(Decimal("0.0001"))


def labour_basis_for(ref: str) -> Optional[dict]:
    """The person's rate and how it was reached — for display and audit."""
    eid = _resolve_id(ref)
    if not eid:
        return None
    e = _calib().get(eid) or {}
    base = e.get("rate_per_hour")
    if base is None:
        return None
    base = Decimal(str(base))
    eff = base * _effective_multiplier()
    return {
        "employee_id": eid,
        "name": e.get("name", ""),
        "base_hourly": base.quantize(Decimal("0.01")),
        "super_rate": SUPER_RATE,
        "oncost_rate": _oncost_rate(),
        "effective_hourly": eff.quantize(Decimal("0.01")),
        "rate_per_minute": (eff / 60).quantize(Decimal("0.0001")),
    }


# --------------------------------------------------------------- sessions ----

@dataclass(frozen=True)
class PrepSession:
    """One recorded prep: who, how long, when — the timer's output."""
    product: str
    who: str                     # employee id or name, as recorded at login
    minutes: Decimal
    recorded_on: date
    venue: Optional[str] = None


def session_cost(s: PrepSession) -> Optional[Decimal]:
    """Dollar cost of one prep session at the recorder's exact rate. None if
    that person has no rate on file — surfaced, not zeroed."""
    r = rate_per_minute_for(s.who)
    if r is None:
        return None
    return s.minutes * r


def product_labour(product: str, sessions: list[PrepSession],
                   on: Optional[date] = None, window_days: int = 90) -> Optional[Decimal]:
    """
    Representative prep-labour dollars for a product: the mean of its recent
    sessions' costs, each costed at its own recorder's rate. None if there are
    no usable (rate-known) sessions.

    Sessions with an unknown rate are excluded from the average rather than
    dragged in as zero — a zero would flatter the number, and flattering errors
    are the dangerous ones.
    """
    from datetime import timedelta
    rel = [s for s in sessions if s.product == product]
    if on is not None:
        start = on - timedelta(days=window_days)
        rel = [s for s in rel if start < s.recorded_on <= on]
    costs = [c for c in (session_cost(s) for s in rel) if c is not None]
    if not costs:
        return None
    return sum(costs) / Decimal(len(costs))
