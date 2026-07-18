"""EatClub metrics — contribution and cannibalisation, venue-agnostic.

Money is Decimal, never float (ARCHITECTURE.md rule 1: COGS subtracts large
similar numbers). Every public function is pure: inputs in, verdict out, no I/O.

How EatClub settles (same for every venue):
  The POS rings the FULL bill at full price. EatClub applies the discount and its
  commission OFF-POS and pays us the net. So on the POS an EatClub table is
  indistinguishable from a full-price one — which is exactly why the window
  subtraction in `cannibalisation` is needed.

  net = bill x (1 - offer_pct - 0.11)      # 11% billed = 10% commission + GST
  The 10% commission is the true ex-GST cost; the GST portion is reclaimable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

GST_DIVISOR = Decimal("1.1")        # inc-GST -> ex-GST
COMMISSION_EX = Decimal("0.10")     # 10% ex-GST (the 11% billed includes GST)
CENTS = Decimal("0.01")


def D(x) -> Decimal:
    """Coerce to Decimal via str, so 3558.17 doesn't arrive as 3558.1699999."""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def money(x) -> Decimal:
    return D(x).quantize(CENTS, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# COGS is deliberately NOT computed here. EatClub's margin impact is purely the
# fees it keeps -- offer discount + 11% commission -- which is what
# scripts/eatclub/giveaway.py measures and the daily aggregator subtracts from
# revenue ("EatClub give-away"). COGS is owned by the daily reporting pipeline
# (real recipe / Lightspeed cost, shown on the dashboard); there is no
# blended-COGS estimate in this package by design.
# Removed 2026-07-19 (Zak: don't recalculate COGS, just wire in the fees).
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Cannibalisation — did EatClub displace full-price trade, or add on top?
#
# The prime directive AND its reverse-causality trap (Zak, 2026-07-11):
# when a night is dying the team MANUALLY lifts the discount to pull people in.
# So a deep-discount night with a weak full-price window is usually a RESCUE, not
# cannibalisation — the dead night caused the deep offer, not the other way round.
# Test the tier and the pre-arrival window BEFORE calling anything cannibalisation.
# --------------------------------------------------------------------------- #

NO_CANNIBALISATION = "NO CANNIBALISATION"
RESCUE = "RESCUE / DEMAND-RESPONSE"
SIGNAL = "CANNIBALISATION SIGNAL"


@dataclass(frozen=True)
class CannibalisationRead:
    verdict: str
    window_incgst: Decimal
    eatclub_bills_incgst: Decimal
    full_price_window: Decimal
    baseline_incgst: Decimal
    delta: Decimal
    delta_pct: Decimal
    breakeven_bills: Decimal   # EatClub bills that would drag full-price to baseline


def assess_dinein(window_incgst, eatclub_bills_incgst, baseline_incgst,
                  offer_tier_standard=True, early_window_weak=False,
                  demand_shock=False) -> CannibalisationRead:
    """Dine-in venues (HG, Stowaway).

    window_incgst        POS revenue in the offer window (e.g. 17:00-20:59),
                         which INCLUDES EatClub full bills.
    eatclub_bills_incgst sum of EatClub full menu values that night.
    baseline_incgst      same-DOW pre-launch window mean.
    offer_tier_standard  False if the discount was lifted above the launch tier.
    early_window_weak    True if the pre-arrival hours were already below baseline.
    demand_shock         True if weather / an obvious external hit.
    """
    window = D(window_incgst)
    bills = D(eatclub_bills_incgst)
    base = D(baseline_incgst)
    full_price = window - bills
    delta = full_price - base
    delta_pct = (delta / base * 100).quantize(Decimal("0.1")) if base else Decimal("0")

    if delta >= 0:
        verdict = NO_CANNIBALISATION
    elif (not offer_tier_standard) or early_window_weak or demand_shock:
        verdict = RESCUE
    else:
        verdict = SIGNAL

    return CannibalisationRead(
        verdict=verdict,
        window_incgst=money(window),
        eatclub_bills_incgst=money(bills),
        full_price_window=money(full_price),
        baseline_incgst=money(base),
        delta=money(delta),
        delta_pct=delta_pct,
        breakeven_bills=money(window - base),
    )


# --------------------------------------------------------------------------- #
# Takeaway substitution — Marilyna's.
# A pickup brand can't cannibalise walk-ins; what it can eat into is delivery
# (Uber Eats + own-driver). The question is substitution, not a dinner window.
# --------------------------------------------------------------------------- #

INCREMENTAL = "INCREMENTAL"
SUBSTITUTION = "CHANNEL SUBSTITUTION"


@dataclass(frozen=True)
class SubstitutionRead:
    verdict: str
    eatclub_incgst: Decimal
    delivery_incgst: Decimal          # Uber Eats + own-driver that night
    total_offpremise: Decimal
    delivery_baseline: Decimal        # same-DOW pre-launch delivery mean
    delta: Decimal
    delta_pct: Decimal


def assess_takeaway(eatclub_incgst, delivery_incgst, delivery_baseline,
                    tol_pct=Decimal("5")) -> SubstitutionRead:
    """Marilyna's. If total off-premise (EatClub + delivery) sits at or below the
    delivery baseline, EatClub merely shifted Uber -> EatClub (cheaper for us, but
    not new covers). Above baseline by more than tol_pct -> incremental demand.
    """
    ec = D(eatclub_incgst)
    deliv = D(delivery_incgst)
    base = D(delivery_baseline)
    total = ec + deliv
    delta = total - base
    delta_pct = (delta / base * 100).quantize(Decimal("0.1")) if base else Decimal("0")
    verdict = INCREMENTAL if delta_pct > tol_pct else SUBSTITUTION
    return SubstitutionRead(
        verdict=verdict,
        eatclub_incgst=money(ec),
        delivery_incgst=money(deliv),
        total_offpremise=money(total),
        delivery_baseline=money(base),
        delta=money(delta),
        delta_pct=delta_pct,
    )
