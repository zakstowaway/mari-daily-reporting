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
# Contribution — net of discount, commission and blended COGS, ex-GST.
# GST-neutral. Labour is EXCLUDED (that belongs to the wage-margin view).
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Contribution:
    menu_ex: Decimal
    discount_ex: Decimal
    commission_ex: Decimal
    net_ex: Decimal
    cogs_ex: Decimal
    contribution: Decimal

    @property
    def contrib_pct_of_net(self) -> Decimal:
        if self.net_ex == 0:
            return Decimal("0")
        return (self.contribution / self.net_ex * 100).quantize(Decimal("0.1"))


def contribution_for_bill(bill_inc, offer_pct, cost_blend) -> Contribution:
    """One redeemed EatClub bill's contribution.

    bill_inc   full menu value, inc-GST, as rung on the POS.
    offer_pct  the discount fraction actually applied (0.25, 0.30, ...).
    cost_blend blended COGS as a fraction of MENU volume, ex-GST — charged on the
               full dish, NOT the discounted price (the kitchen cooks the whole
               plate; the discount comes off the price, not the food).
    """
    menu_ex = D(bill_inc) / GST_DIVISOR
    discount_ex = menu_ex * _as_fraction(offer_pct)
    commission_ex = menu_ex * COMMISSION_EX
    net_ex = menu_ex - discount_ex - commission_ex
    cogs_ex = menu_ex * D(cost_blend)
    return Contribution(
        menu_ex=money(menu_ex),
        discount_ex=money(discount_ex),
        commission_ex=money(commission_ex),
        net_ex=money(net_ex),
        cogs_ex=money(cogs_ex),
        contribution=money(net_ex - cogs_ex),
    )


def weekly_contribution(rows, cost_blend) -> Contribution:
    """Aggregate a week (or any set) of PAID redemptions.

    rows: iterable of dicts with 'bill_full' (inc-GST) and 'offer_pct' (as a
    percentage, e.g. 25, or a fraction 0.25 — both accepted). UNREDEEMED rows
    (blank bill) are skipped: an unredeemed offer costs nothing.
    """
    tot = {k: Decimal("0") for k in
           ("menu_ex", "discount_ex", "commission_ex", "net_ex", "cogs_ex", "contribution")}
    for r in rows:
        bill = r.get("bill_full")
        if bill in (None, "", "None"):
            continue
        c = contribution_for_bill(bill, r["offer_pct"], cost_blend)
        tot["menu_ex"] += c.menu_ex
        tot["discount_ex"] += c.discount_ex
        tot["commission_ex"] += c.commission_ex
        tot["net_ex"] += c.net_ex
        tot["cogs_ex"] += c.cogs_ex
        tot["contribution"] += c.contribution
    return Contribution(**{k: money(v) for k, v in tot.items()})


def _as_fraction(offer) -> Decimal:
    o = D(offer)
    return o / 100 if o > 1 else o


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
