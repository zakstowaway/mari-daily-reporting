"""Canonical per-venue config used across scripts.

Every venue-scoped script (daily_deputy_pull, daily_aggregator, future
weekly rollups) reads its runtime config from here so we don't
hard-code OU names, prefixes, or subject lines in half a dozen places.

Add a new venue by appending to VENUES. Fields:

  display_name       — human label, used in logs / dashboard
  file_prefix        — filename token for outputs (e.g. `data/mari_daily_*.json`)
  baseline_file      — baselines/<file> — targets, alerts, seasonal factors
  insights_subject   — Pipedream matches on the exact scheduled-report subject
  event_type         — GitHub repository_dispatch type Pipedream sends
  deputy_ous         — allow-list per department; matched exactly to
                       OperationalUnitName in Deputy
  lane_config        — which KPI lanes to compute; Stow has no delivery so
                       we substitute GP%.

The `venue` CLI arg to every venue-scoped script is a key in this dict
(e.g. `marilynas`, `stowaway`, `harry`). Default is `marilynas` for
backwards compat with the pre-refactor pipeline.

2026-07-12 — aligned with the weekly-report pipeline (deputy_config.py):
  - "Harry's Bar" added to HG FOH (was missing — HG bar shifts were dropped)
  - "Driver" OU added to Marilynas (Uber-Direct / own-driver shifts)
  - Admin OU split 90/10 Stowaway/HarryGatos (worked admin time only) —
    matches deputy_config.ADMIN_*_SHARE in the weekly wages builder.
  - Monday reallocation: Stow Kitchen shifts on Mondays belong to
    HarryGatos/Kitchen (the Stow kitchen doesn't open Mondays; only HG works
    through that POS). Mirrors _reallocate_monday_kitchen in
    build_wages_from_deputy.py.
"""

# Deputy Admin OU — worked admin time splits across the two bar/restaurant
# venues. Marilynas gets no admin share (matches weekly-report canon).
ADMIN_OU_NAME = "Admin"
ADMIN_SHARES = {"stowaway": 0.9, "harry": 0.1}

# Superannuation Guarantee rate. Deputy's Cost field is ex-super, so wages get
# grossed up by this.
#
# ESTIMATE OF LAST RESORT — do not reach for it if Xero can answer (2026-07-18).
# Two ways this is wrong as a flat rate:
#
#   1. Super is payable on ORDINARY TIME earnings. Overtime and some allowances
#      attract none, so even today nobody is at exactly 12%: week ending
#      2026-07-12 ran 11.79% overall — Herminder Khera 12.00% (salaried, no OT),
#      David Armour 11.31%.
#   2. The SG rate CHANGED. 11% from 1 Jul 2023, 11.5% from 1 Jul 2024, 12% from
#      1 Jul 2025. Most of our history predates 12% entirely.
#
# Measured against 100 Xero pay runs: actual super is $289,768.94 on
# $2,587,065.11 of wages — 11.201%. A flat 12% books $310,447.81, overstating by
# $20,678.87. By financial year: FY24-25 10.93%, FY25-26 11.37%, FY26-27 11.71%.
#
# rebuild_wages now uses Xero's per-person actuals (data/xero_super_weekly.json)
# and only falls back to this for the open week and for people payroll has never
# paid. daily_aggregator and roster_pull still use it — they run before Xero
# knows anything, and rebuild_wages corrects them afterwards.
SUPER_RATE = 0.12

# OU that must flip venue on Mondays: Stow Kitchen -> HarryGatos Kitchen.
MONDAY_REALLOCATED_OU = "Stow Kitchen"

VENUES = {
    "marilynas": {
        "display_name": "Marilyna's Pizza",
        "file_prefix": "mari",
        "baseline_file": "mari_baseline.json",
        "insights_subject": "Mari Daily Sales Auto",
        "event_type": "insights-csv-arrived",
        "deputy_ous": {
            "Kitchen": ["Pizza Shop"],
            "Driver":  ["Driver"],
        },
        "lane_config": ["revenue", "cogs", "wages", "delivery"],
    },
    "stowaway": {
        "display_name": "Stowaway Bar",
        "file_prefix": "stow",
        "baseline_file": "stow_baseline.json",
        "insights_subject": "Stow Daily Sales Auto",
        "event_type": "stow-csv-arrived",
        "deputy_ous": {
            "Kitchen": ["Stow Kitchen"],
            "FOH":     ["Stow Bar", "Stow Floor"],
            "Driver":  [],  # no delivery at Stow
        },
        "lane_config": ["revenue", "cogs", "wages", "gp"],
    },
    "harry": {
        "display_name": "Harry Gatos",
        "file_prefix": "hg",
        "baseline_file": "hg_baseline.json",
        "insights_subject": "HG Daily Sales Auto",
        "event_type": "hg-csv-arrived",
        "deputy_ous": {
            "Kitchen": ["Harry's Kitchen"],
            "FOH":     ["Harry's Bar", "Harry's Floor"],
            "Driver":  [],  # no delivery at HG
        },
        "lane_config": ["revenue", "cogs", "wages", "gp"],
    },
}


def get(venue_key: str) -> dict:
    """Return the config for a venue key, raising a clear error if unknown."""
    if venue_key not in VENUES:
        raise KeyError(
            f"Unknown venue {venue_key!r}. Known: {list(VENUES)}"
        )
    return VENUES[venue_key]


def kitchen_ous(venue_key: str) -> set:
    return set(get(venue_key)["deputy_ous"].get("Kitchen", []))


def foh_ous(venue_key: str) -> set:
    return set(get(venue_key)["deputy_ous"].get("FOH", []))


def driver_ous(venue_key: str) -> set:
    return set(get(venue_key)["deputy_ous"].get("Driver", []))


def all_ous(venue_key: str) -> set:
    """Union of every OU across every dept — for the exclusion filter."""
    cfg = get(venue_key)
    out = set()
    for dept_ous in cfg["deputy_ous"].values():
        out.update(dept_ous)
    return out


def dept_for_ou(venue_key: str, ou_name: str) -> str | None:
    """Return the dept ('Kitchen' | 'FOH' | 'Driver') for an OU name, or None."""
    cfg = get(venue_key)
    for dept, ous in cfg["deputy_ous"].items():
        if ou_name in ous:
            return dept
    return None
