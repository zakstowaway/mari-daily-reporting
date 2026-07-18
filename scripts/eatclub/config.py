"""Per-venue EatClub configuration and Reporting-Group attribution.

Kept OUT of core/venues.py deliberately: venues.py is the load-bearing domain
module with 8 importers, and this is EatClub-only config that nothing else needs.
Config is data (ARCHITECTURE.md); this file is the data.

Marilyna's has no till — it is Reporting Groups on the Stowaway POS. So the split
between Stowaway-proper and Marilyna's is done here by RG, mirroring
daily_aggregator.classify_product EXACTLY (same normalisation, same sets).
"""

from __future__ import annotations

# --- RG attribution, copied verbatim from daily_aggregator.py (normalised) ---
# Matching is strip().lower() then drop a trailing ' [harrys]'. The literals here
# are already in that normalised (lowercase) form.

MARILYNAS_RGS = {
    "marilyna's pizza", "marilynas pizza",
    "marilyna's soft drinks", "marilynas soft drinks",
    "add-ons - pizza", "dine-in pizza",
    "delivery alcohol",
}
HG_FOOD_RG = "harry gatos food"

# Product-name override: '$60 BANQUET' is Mari but its RG is missing from the
# generated map (daily_aggregator.PRODUCT_OVERRIDES). Carry it or Mari undercounts.
MARILYNAS_PRODUCT_OVERRIDES = {"$60 BANQUET": "m"}


def norm_rg(rg: str) -> str:
    """Identical to daily_aggregator._norm_rg."""
    k = (rg or "").strip().lower()
    if k.endswith(" [harrys]"):
        k = k[: -len(" [harrys]")]
    return k


def is_marilynas_row(reporting_group: str, product_name: str = "") -> bool:
    if norm_rg(reporting_group) in MARILYNAS_RGS:
        return True
    return (product_name or "").strip() in MARILYNAS_PRODUCT_OVERRIDES


def is_stowaway_proper_row(reporting_group: str, product_name: str = "") -> bool:
    """Everything on the Stow site that is NOT Marilyna's and NOT HG-food-on-Stow.
    This is the set whose 17:00-20:59 sum is Stowaway's dinner window."""
    if is_marilynas_row(reporting_group, product_name):
        return False
    if norm_rg(reporting_group) == HG_FOOD_RG:
        return False
    return True


# --- EatClub program config per venue ---------------------------------------
# NOTE: COGS is intentionally NOT held here. EatClub's margin impact is the fees
# only (discount + commission); COGS comes from the daily reporting pipeline's
# real recipe/Lightspeed cost (shown on the dashboard). See eatclub/giveaway.py.
#
# window_hours: the offer window for dine-in venues, POS hour buckets.
# launch_date : EatClub go-live. Baseline is the 8 weeks BEFORE this. None = not
#               live yet (Stowaway is still behind the EatClub onboarding tour;
#               Marilyna's date TBC). Cannibalisation must refuse to run until set.

VENUE_EATCLUB = {
    "harry": {
        "channel": "dine-in",
        "site_id": 151095,
        "window_hours": (17, 18, 19, 20),
        "launch_date": "2026-07-01",
        "baseline_window": ("2026-05-06", "2026-06-30"),
        "eatclub_login": "kris@stowawaybar.com",
        # HG is its own Lightspeed site; hourly comes from salesummarybyhour.
        "hourly_source": "salesummarybyhour",
    },
    "stowaway": {
        "channel": "dine-in",
        "site_id": 150764,                 # shared with Marilyna's
        "window_hours": (17, 18, 19, 20),
        "launch_date": None,               # not live — behind onboarding tour
        "baseline_window": None,           # set 8 weeks pre-launch once dated
        "eatclub_login": "kris@stowawaybar.com",
        # Shared till: salesummarybyhour can't separate Mari. Hourly must come
        # from the Custom Insights 'Stow Hourly RG Auto' feed, split by RG.
        "hourly_source": "custom_insights_hourly_rg",
        "row_filter": "stowaway_proper",
    },
    "marilynas": {
        "channel": "takeaway",
        "site_id": 150764,                 # rides the Stow till
        "window_hours": None,              # no dine-in window — substitution model
        "launch_date": None,               # TBC
        "baseline_window": None,
        "eatclub_login": "kris@stowawaybar.com",
        # Baseline is delivery, not a window: mari_daily_history.csv delivery_dollars.
        "baseline_metric": "delivery_dollars",
        "hourly_source": "custom_insights_hourly_rg",
        "row_filter": "marilynas",
    },
}
