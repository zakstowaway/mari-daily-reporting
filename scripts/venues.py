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
"""

VENUES = {
    "marilynas": {
        "display_name": "Marilyna's Pizza",
        "file_prefix": "mari",
        "baseline_file": "mari_baseline.json",
        "insights_subject": "Mari Daily Sales Auto",
        "event_type": "insights-csv-arrived",
        "deputy_ous": {
            "Kitchen": ["Pizza Shop"],
            "Driver":  [],  # add own-driver OU here if Zak sets one up
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
            "FOH":     ["Harry's Floor"],
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
