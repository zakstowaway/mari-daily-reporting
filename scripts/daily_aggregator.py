"""
Daily aggregator — runs each morning after Insights CSV lands.

Inputs (per venue):
  - data/insights_<prefix>_<yyyy-mm-dd>.csv   (Lightspeed Insights daily report;
                                                may be a ZIP wrapping the CSV;
                                                supports Sales-Summary and
                                                Sales-by-Product schemas)
  - data/deputy_<prefix>_<yyyy-mm-dd>.json    (Deputy API daily wages, ex-super;
                                                salaried costs synthesized by
                                                daily_deputy_pull.py)
  - data/payments_<prefix>_<yyyy-mm-dd>.csv   (OPTIONAL — Insights Sales by
                                                Payment Type; gives real Uber
                                                Eats revenue.)
  - data/manual/uber_direct.json              (OPTIONAL — Zak-entered weekly
                                                Uber Direct fee totals,
                                                amortized across the week.)

  Marilynas falls back to unprefixed insights_<date>.csv / deputy_<date>.json
  for backwards compat with the existing daily_pull workflow.

2026-07-12 — aligned with the LIVE weekly-report pipeline
(Daily Sales/skill-patches/weekly-report/scripts — not the stale packaged
skill):
  - Venue attribution (matches build_rich_rollups.py):
      * Marilynas carve-out EXCLUDES 'Delivery Kitchen' (removed 2026-05-13:
        it IS Stow Kitchen food on Uber — revenue and labour belong with
        Stow Kitchen).
      * Symmetric cross-venue food reallocation: Stow rows tagged
        'Harry Gatos Food' -> HarryGatos; HG rows tagged 'Stow Food' ->
        Stowaway. The aggregator reads the SIBLING venue's insights CSV for
        the same date (when present) and pulls its reallocated rows in.
  - Dept split (matches classify_rg_to_dept in build_weekly_report.py):
      Kitchen = explicit RG set (+ Desserts + Delivery Kitchen per the
      canonical dept-takings table); FOH/bev is the CATCH-ALL — anything
      not Kitchen (incl. Unmapped/Modifiers) is FOH.
  - Wages are grossed up by 12%% super (venues.SUPER_RATE) so every wage
    figure is inc-super, same as wages_weekly.csv TotalWagesIncSuper.
    Deputy JSON now carries an 'Admin' dept (90/10 split, from the pull).
    Marilynas total wages INCLUDE Driver (matches Mari Venue Total in the
    weekly canon); Driver dollars also surface in the delivery lane.
  - Marilynas Net Wage %%: when real/estimated Uber fees are known, net
    takings = rev_ex - uber fees, and net_wage_pct = wages / net takings.
    Weekly canon: Net is the operationally meaningful number.
  - History CSV is NO LONGER trimmed to 90 days — full history is kept
    (backfilled from the product masters via scripts/backfill_history.py).

Kitchen / FOH split classification comes from scripts/product_dept_map.json —
generated from the LIVE reporting_group_mapping.csv + the rules above.
DO NOT hand-edit keyword rules here; regenerate the map so daily and weekly
reporting stay consistent. If the CSV carries a Category / Reporting Group
column it wins over the product-name lookup.

Footer totals rows (empty product name) are dropped before any summing —
the scheduled Insights CSV ends with one and it doubles revenue otherwise.

Output:
  - data/<prefix>_daily_<yyyy-mm-dd>.json   (per-day rollup with alerts)
  - data/<prefix>_daily_history.csv         (full history, backfilled)

CLI:
  python daily_aggregator.py                        # yesterday, Mari
  python daily_aggregator.py 2026-07-11             # specific date, Mari
  python daily_aggregator.py --venue stowaway 2026-07-11
  python daily_aggregator.py --venue harry 2026-07-11
"""
import csv, io, json, os, sys, zipfile
from pathlib import Path
from datetime import date, timedelta, datetime

sys.path.insert(0, str(Path(__file__).parent.parent))   # repo root -> core/, modules/


def _load_our_costs(venue_key, target):
    """
    product name -> our cost per serve on `target`, from our own recipes.

    Returns {} and carries on if there are no recipes yet, or if anything in
    the recipe module is unhappy. This runs unattended at 6am and its job is
    the daily numbers -- a recipe problem must not take the whole pull down.
    Falling back to Lightspeed's cost is a known, visible state
    (cost_source='lightspeed'), not a silent one.

    AS-OF, not current: costing 16 July uses 16 July's prices, whenever it runs.
    See ARCHITECTURE.md decision 2.
    """
    try:
        from core.domain import CostSeries, load_cost_observations
        from modules.recipes.cost import MissingCost, cost_on, load_recipes, recipe_as_of

        venue_file = {"stowaway": "stowaway", "harry": "harry_gatos",
                      "marilynas": "marilynas"}.get(venue_key, venue_key)
        recipes = load_recipes(venue_file)
        if not recipes:
            return {}
        costs = CostSeries(load_cost_observations())
        out = {}
        for product in {r.product for r in recipes}:
            r = recipe_as_of(recipes, product, target)
            if not r:
                continue
            try:
                out[product] = float(cost_on(r, costs, target))
            except MissingCost as e:
                # Refusing to cost one dish is correct; it must not stop the pull.
                print(f"  recipe cost skipped: {e}")
        if out:
            print(f"  our recipes cost {len(out)} product(s) on {target}")
        return out
    except Exception as e:                                  # noqa: BLE001
        print(f"  recipe costing unavailable ({e}) — using Lightspeed's cost")
        return {}
from core import venues as V
from wage_model import super_lookup

REPO_ROOT = Path(os.environ.get("REPO_ROOT", "."))
DATA_DIR = REPO_ROOT / "data"
BASELINES_DIR = REPO_ROOT / "baselines"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEPT_MAP_FILE = Path(__file__).parent / "product_dept_map.json"

# Uber Eats marketplace commission. 30% verified against the Uber Payouts page
# (weekly-report skill, known-margins.md). NOTE: marketing + tablet fees are
# NOT included here — the weekly report nets those out separately from
# merchants.ubereats.com. This lane is the commission estimate only.
UBER_COMMISSION_RATE = 0.30

# Super gross-up: Deputy Cost is ex-super; weekly canon reports inc-super.
SUPER_MULT = 1.0 + V.SUPER_RATE


def read_insights_csv_text(path: Path) -> str:
    """Return CSV text from an Insights payload that may be a raw CSV or a ZIP."""
    raw = path.read_bytes()
    if raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            csv_members = [m for m in zf.namelist() if m.lower().endswith(".csv")]
            if not csv_members:
                raise ValueError(f"ZIP payload has no .csv members: {zf.namelist()}")
            largest = max(csv_members, key=lambda m: zf.getinfo(m).file_size)
            print(f"  Unwrapped ZIP -> using member {largest!r} ({zf.getinfo(largest).file_size} bytes)")
            return zf.read(largest).decode("utf-8-sig", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def parse_num(x) -> float:
    """Parse a Kounta-Insights currency/number cell."""
    if x is None:
        return 0.0
    s = str(x).strip()
    if not s:
        return 0.0
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace("%", "").strip()
    if not s:
        return 0.0
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if negative else v


def col(r: dict, *candidates: str) -> str:
    """First non-empty value across candidate column names."""
    for c in candidates:
        v = r.get(c)
        if v not in (None, ""):
            return v
    return ""


# --------------------------------------------------------------
# Product -> department classification (LIVE weekly-report canon)
#   'f'   food / Kitchen
#   'b'   bev / FOH (CATCH-ALL — anything not otherwise classified)
#   'm'   Marilynas ride-on (Stow POS rows that are Mari P&L)
#   'hgf' Harry Gatos Food rung on the Stow POS -> HarryGatos Kitchen
#   'stf' Stow Food rung on the HG POS        -> Stowaway Kitchen
# --------------------------------------------------------------
_DEPT_MAP = None

def _load_dept_map():
    global _DEPT_MAP
    if _DEPT_MAP is None:
        if DEPT_MAP_FILE.exists():
            with DEPT_MAP_FILE.open() as f:
                _DEPT_MAP = json.load(f)
        else:
            print(f"WARNING: {DEPT_MAP_FILE} missing — no food/bev split possible")
            _DEPT_MAP = {"*": {}, "stow": {}, "hg": {}}
    return _DEPT_MAP

MARILYNAS_RGS = {
    "marilyna's pizza", "marilynas pizza",
    "marilyna's soft drinks", "marilynas soft drinks",
    "add-ons - pizza", "dine-in pizza",
    "delivery alcohol",
    # 'delivery kitchen' REMOVED 2026-05-13 — it's Stow Kitchen food on Uber.
    # 'delivery cocktails' REMOVED 2026-07-16 (Zak) — it's STOW revenue. Alcohol
    # on delivery is Mari's, cocktails on delivery are the bar's. They sat in the
    # same line here purely because both had "delivery" in the name.
}
# NOTE — this set is P&L ATTRIBUTION: whose till-line is this? It is deliberately
# WIDER than the weekly-report skill's "Marilynas-strict" set
# (references/reporting-groups.md), which excludes Dine-in Pizza as
# "substitutable, not incremental". Both are right: strict answers "what would we
# lose if Mari closed?", this answers "whose revenue is it?". Don't reconcile them.
FOOD_RGS = {'big plates','small plates','kitchen specials','salads','desserts','kids meals','kids',
            'add-ons - kitchen','delivery kitchen','sides','mains','snacks','yum cha','staff dinners'}
HG_FOOD_RG = 'harry gatos food'
STOW_FOOD_RG = 'stow food'


def _norm_rg(rg: str) -> str:
    k = (rg or '').strip().lower()
    if k.endswith(' [harrys]'):
        k = k[:-len(' [harrys]')]
    return k


def _rg_dept(rg: str, venue_key: str) -> str | None:
    """RG-level classification. Returns None when the RG is unknown/blank."""
    k = _norm_rg(rg)
    if not k:
        return None
    if venue_key == "stowaway":
        if k in MARILYNAS_RGS:
            return 'm'
        if k == HG_FOOD_RG:
            return 'hgf'
    if venue_key == "harry" and k == STOW_FOOD_RG:
        return 'stf'
    if k in FOOD_RGS or k in (HG_FOOD_RG, STOW_FOOD_RG):
        return 'f'
    return 'b'   # FOH catch-all — matches classify_rg_to_dept in the weekly report


# Products the generated map has never heard of (2026-07-17).
#
# product_dept_map.json is built from the weekly report's
# reporting_group_mapping.csv — a HISTORICAL aggregate. A product missing from
# it falls through to the 'b' FOH catch-all, and for a Marilyna's product that
# is silently a DOUBLE COUNT: her report contains it, so she banks it, and Stow
# doesn't recognise it as 'm', so Stow never strips it. Both venues bill it.
#
# '$60 BANQUET' — Mari's (Zak, 2026-07-17). $54.55 ex every time it sells;
# caught reconciling against Lightspeed's own site footer, which is the only
# number in this pipeline that isn't derived from our own code. Found on 2 of
# the 11 days we hold — ~$3,620/yr. Its sibling '$45 FEAST' IS mapped to 'm',
# which is exactly why nobody noticed the gap.
#
# This is the mirror of the Mari coverage leak: that one had the classifier
# saying "Mari's" when her report didn't have it; this has her report saying
# "Mari's" when the classifier doesn't. Same root — Stow strips by CLASSIFIER
# while Mari counts by REPORT, and any daylight between the two definitions
# leaks money one way or doubles it the other.
#
# Proper fix is upstream in reporting_group_mapping.csv, which lives in the
# weekly-report skill and is read-only from here. This overlay survives a map
# regeneration; delete an entry once the source knows about it.
PRODUCT_OVERRIDES = {
    "$60 BANQUET": "m",
}


def classify_product(row: dict, product_name: str, venue_key: str) -> str:
    """-> 'f' | 'b' | 'm' | 'hgf' | 'stf'.

    Prefers an explicit Reporting Group / Category column when the CSV has
    one; otherwise resolves product name through the canonical map, falling
    back to 'b' (FOH catch-all, same as the weekly classifier).
    """
    rg = col(row, "Reporting Group Name", "Reporting Group", "Category")
    if rg:
        d = _rg_dept(rg, venue_key)
        if d is not None:
            return d
    m = _load_dept_map()
    vkey = {"stowaway": "stow", "harry": "hg"}.get(venue_key)
    if vkey is None:
        return 'f'   # Mari: everything is Kitchen; split not used
    pn = (product_name or '').strip()
    if pn in PRODUCT_OVERRIDES:
        return PRODUCT_OVERRIDES[pn]
    return m.get(vkey, {}).get(pn) or m.get("*", {}).get(pn) or 'b'


# --------------------------------------------------------------
# CLI parsing
# --------------------------------------------------------------
venue_key = "marilynas"
target = None
args = sys.argv[1:]
i = 0
while i < len(args):
    a = args[i]
    if a == "--venue":
        venue_key = args[i + 1]
        i += 2
        continue
    try:
        target = date.fromisoformat(a)
    except ValueError:
        pass
    i += 1

if target is None:
    target = date.today() - timedelta(days=1)

cfg = V.get(venue_key)
prefix = cfg["file_prefix"]
lanes = set(cfg["lane_config"])
split_venue = venue_key in ("stowaway", "harry")   # Mari is Kitchen-only, no split
print(f"Aggregating {venue_key} ({cfg['display_name']}) for: {target.isoformat()}")


def resolve(*candidates: Path) -> Path | None:
    for c in candidates:
        if c.exists():
            return c
    return None


def load_product_rows(path: Path):
    """Parse an Insights product CSV -> (rows, fieldnames), footer dropped."""
    csv_text = read_insights_csv_text(path)
    reader = csv.DictReader(io.StringIO(csv_text))
    all_rows = list(reader)
    fieldnames = reader.fieldnames or []
    if any(c in fieldnames for c in ("Product Name", "Product")):
        footer_rows = [r for r in all_rows if not (r.get("Product Name") or r.get("Product") or "").strip()]
        if footer_rows:
            footer_rev = sum(parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales", "Sale Amount", "Total Sales")) for r in footer_rows)
            print(f"  Dropped {len(footer_rows)} footer/subtotal row(s) with no product name (${footer_rev:,.2f} inc-GST)")
            all_rows = [r for r in all_rows if (r.get("Product Name") or r.get("Product") or "").strip()]
    return all_rows, fieldnames


def row_rev(r):
    return parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales", "Sale Amount", "Total Sales"))


def row_cogs(r):
    c = parse_num(col(r, "COGS", "Cost", "Cost of Goods Sold"))
    # Guard against Lightspeed recipe-cost typos (e.g. Serpents Kiss Schooner
    # at $145,158.75/unit, Sep 2025): absurd costs are treated as missing.
    if c > max(5 * row_rev(r), 500):
        return 0.0
    return c


# --------------------------------------------------------------
# Load Insights CSV
# --------------------------------------------------------------
# ---- MARILYNA'S COMES OFF THE STOW TILL (2026-07-17) ----
#
# Marilyna's has no till. Her export was only ever a FILTER over the Stow POS —
# a saved Lightspeed schedule with a Reporting Group list on it. Sourcing her
# P&L from that filter made her numbers hostage to a setting nobody versions:
#
#   * the filter drops a group  -> Stow strips those rows, her report never gets
#     them, the revenue reaches NO venue. $612.70 on 14 Jul, $375.84 on 11 Jul,
#     $235.71 on 15 Jul, unnoticed for days.
#   * the filter GAINS a group  -> her report bills it and Stow doesn't strip it.
#     Both venues keep it. '$60 BANQUET', $54.55 a time.
#   * the filter CHANGES        -> history splits. Delivery Cocktails were hers
#     until 16 Jul and Stow's after, so one map cannot be right for both eras.
#     That is the ~$43/day on 10-11 Jul, and it is unfixable while her revenue
#     depends on what the filter happened to say that morning.
#
# So: take her rows off the STOW till and classify them like everything else.
# The till is the whole site — nothing can go missing from it, and Stow strips
# exactly what Mari receives, so the two cannot disagree. One map, one rule,
# every day, past and future. Verified against Lightspeed's own reporting groups:
# reproduces their Mari total EXACTLY (0.00) on every day whose export carries a
# tax column — 11, 13, 14, 15, 16, 17 Jul.
#
# Her own export is still pulled — as a CROSS-CHECK, not a source. If it ever
# disagrees with the till, that's the filter drifting and we want to hear about
# it. It just can't move a number any more.
if venue_key == "marilynas":
    insights_file = resolve(DATA_DIR / f"insights_stow_{target.isoformat()}.csv")
    if insights_file is None:
        print(f"  Mari needs the STOW export (she has no till of her own) — not found.")
else:
    insights_file = resolve(DATA_DIR / f"insights_{prefix}_{target.isoformat()}.csv")

if insights_file is None:
    print(f"Insights CSV not found for {venue_key} {target.isoformat()}")
    print("Will emit alert-only record with 'data_missing' flag")
    lightspeed_data = None
else:
    all_rows, fieldnames = load_product_rows(insights_file)
    print(f"  Parsed {len(all_rows)} rows; columns: {fieldnames}")

    # Mari is the 'm' slice of the Stow till. Classify against 'stowaway' — the
    # rows ARE Stow-till rows, and classify_product('marilynas') short-circuits
    # to 'f' (her split is Kitchen-only) which would tag every row on the site.
    if venue_key == "marilynas":
        _n = lambda r: (r.get("Product Name") or r.get("Product") or "").strip()
        all_rows = [r for r in all_rows if classify_product(r, _n(r), "stowaway") == 'm']
        print(f"  Marilyna's = {len(all_rows)} 'm' rows off the Stow till "
              f"(${sum(row_rev(r) for r in all_rows):,.2f} inc)")

    # ---- classify every row once ----
    row_depts = [
        classify_product(r, (r.get("Product Name") or r.get("Product") or "").strip(), venue_key)
        for r in all_rows
    ]

    # ---- exclusions: rows that are ANOTHER venue's P&L ----
    #   stowaway: 'm' (Marilynas ride-on) + 'hgf' (HarryGatos food)
    #   harry:    'stf' (Stowaway food)
    if split_venue:
        excl_tags = {"stowaway": {"m", "hgf"}, "harry": {"stf"}}[venue_key]
        rows = [r for r, d in zip(all_rows, row_depts) if d not in excl_tags]
        excluded = len(all_rows) - len(rows)
        if excluded:
            excl_rev = sum(row_rev(r) for r, d in zip(all_rows, row_depts) if d in excl_tags)
            print(f"  Excluded {excluded} cross-venue rows ({sorted(excl_tags)}) from {venue_key} totals (${excl_rev:,.2f} inc)")
        elif venue_key == "stowaway":
            # ---- narrowed-report tripwire (2026-07-16) ----
            # Stow's export is the FULL SITE report on purpose. Marilyna's ('m')
            # and Harry Gatos food ('hgf') ring through the Stow till, and two
            # other venues read those rows OUT of this file:
            #   * Mari  — the coverage guard below compares her report against
            #             the 'm' rows here; no 'm' rows = the guard goes blind
            #             and can never fire again.
            #   * HG    — the reallocation above LIFTS 'hgf' rows out of this
            #             file (~$585/day, ~$213k/yr, concentrated on Mondays:
            #             07-06 $3,233, 07-13 $2,544). Not here = reaches no venue.
            #
            # Stow's own totals never included these rows — they're stripped
            # right here — so a "clean up Stow's report to only Stow RGs" change
            # looks harmless from inside Lightspeed and costs HG six figures a
            # year in silence. Nearly shipped 2026-07-16.
            #
            # Mari rings through the Stow till EVERY trading day (min 2 rows on
            # the quietest Monday in 10 days sampled), so zero cross-venue rows
            # means the report got narrowed, not that nobody ordered pizza.
            print(f"  *** STOW EXPORT LOOKS NARROWED: 0 cross-venue rows in {insights_file.name}.")
            print(f"      This file is meant to be the FULL SITE report — Mari and Harry Gatos")
            print(f"      read their revenue out of it. Stow's own totals are unaffected either")
            print(f"      way, so this will NOT show up as a Stow discrepancy.")
            print(f"      Check the Lightspeed email report filter includes ALL reporting groups.")
    else:
        rows = all_rows

    # ---- cross-venue INBOUND rows (symmetric food reallocation) ----
    # Stow gains HG-file 'stf' rows; HG gains Stow-file 'hgf' rows.
    cross_rows = []
    if split_venue:
        sib_prefix, sib_key, want = (
            ("hg", "harry", "stf") if venue_key == "stowaway" else ("stow", "stowaway", "hgf")
        )
        sib_file = resolve(DATA_DIR / f"insights_{sib_prefix}_{target.isoformat()}.csv")
        if sib_file is not None:
            sib_rows, _ = load_product_rows(sib_file)
            for r in sib_rows:
                d = classify_product(r, (r.get("Product Name") or r.get("Product") or "").strip(), sib_key)
                if d == want:
                    cross_rows.append(r)
            if cross_rows:
                cross_rev = sum(row_rev(r) for r in cross_rows)
                print(f"  Pulled {len(cross_rows)} reallocated rows from {sib_prefix} CSV (${cross_rev:,.2f} inc) -> {venue_key} Kitchen")
        else:
            # ---- sibling-race tripwire (2026-07-17) ----
            # Each venue's pull is fired by Pipedream the moment ITS OWN Insights
            # email lands, so the venues aggregate in arrival order — not in
            # dependency order. This venue's Kitchen revenue is partly rung on the
            # sibling's till and lives in the sibling's CSV. If that CSV has not
            # arrived yet, we silently record a venue that is missing revenue.
            #
            # Observed 2026-07-16: hg-csv-arrived 19:02, stow-csv-arrived 19:30.
            # Harry Gatos aggregated 28 minutes before Stow's CSV existed, so it
            # pulled 0 rows and recorded $802.67 instead of $814.88. Only $12.21
            # that day — but Harry Gatos' food is on the Stow till in VOLUME on
            # Mondays: 07-06 $3,233.59, 07-13 $2,543.91 (mean ~$533/day overall,
            # ~$195k/yr). A Monday race costs three grand and looks like a quiet
            # trading day.
            #
            # The 12:10pm re-aggregation cron re-runs every venue and repairs this
            # incidentally (it exists for Deputy approvals, not for this), so the
            # damage is normally a wrong number between ~6am and midday. This
            # shouts so that a race is visible rather than inferred — and so that
            # a day where the sibling CSV NEVER arrives cannot pass silently.
            print(f"  *** SIBLING CSV MISSING: insights_{sib_prefix}_{target.isoformat()}.csv not found.")
            print(f"      {venue_key} Kitchen revenue rung on the {sib_prefix} till CANNOT be reallocated,")
            print(f"      so this day is UNDERSTATED for {venue_key}. Usually a race — {sib_prefix}'s")
            print(f"      Insights email had not landed when {venue_key}'s pull fired.")
            print(f"      The 12:10pm re-aggregation should repair it; if the CSV never arrives, it won't.")

    rows = rows + cross_rows

    # ---- Mari cross-check (2026-07-17) ----
    # Her revenue no longer comes from her export — it's the 'm' slice of the
    # Stow till (see the file resolution above). So her export can't move a
    # number any more; it's now a witness. If the two disagree, the Lightspeed
    # filter has drifted from MARILYNAS_RGS and somebody should know.
    #
    # This replaces three guards that only existed because her export WAS the
    # source: RECOVERED (filter dropped a group -> revenue reached no venue),
    # DOUBLE COUNTED (filter gained one -> both venues billed it), and DEDUP
    # UNSOUND (name-matching between two sources that no longer both exist).
    # None of those failures are reachable now: the till is the whole site, and
    # Stow strips exactly what Mari receives.
    if venue_key == "marilynas":
        _own = resolve(DATA_DIR / f"insights_mari_{target.isoformat()}.csv",
                       DATA_DIR / f"insights_{target.isoformat()}.csv")
        if _own is not None:
            _orows, _ = load_product_rows(_own)
            _theirs = sum(row_rev(r) for r in _orows)
            _ours = sum(row_rev(r) for r in rows)
            if abs(_theirs - _ours) > 0.02:
                print(f"  *** MARI FILTER DRIFT: her Lightspeed export says ${_theirs:,.2f} inc,")
                print(f"      the Stow till's 'm' rows say ${_ours:,.2f} inc — a ${_theirs - _ours:+,.2f} gap.")
                print(f"      Her numbers come from the TILL, so this changes nothing — but the")
                print(f"      'Mari Daily Sales Auto' Reporting Group filter no longer matches")
                print(f"      MARILYNAS_RGS. Reconcile the two before they drift further.")
                _mine = {(r.get("Product Name") or r.get("Product") or "").strip() for r in rows}
                _hers = {(r.get("Product Name") or r.get("Product") or "").strip() for r in _orows}
                if _hers - _mine:
                    print(f"        in her export, not 'm' on the till: {sorted(_hers - _mine)[:5]}")
                if _mine - _hers:
                    print(f"        'm' on the till, not in her export: {sorted(_mine - _hers)[:5]}")

    revenue_inc = sum(row_rev(r) for r in rows)
    total_tax = sum(parse_num(col(r, "Total Tax", "GST", "Tax")) for r in rows)
    revenue_net_explicit = sum(parse_num(col(r, "Revenue_net", "NetRevenue", "Net Sales")) for r in rows)
    if revenue_net_explicit > 0:
        revenue_net = revenue_net_explicit
    elif total_tax > 0:
        revenue_net = revenue_inc - total_tax
    else:
        revenue_net = revenue_inc / 1.1

    cogs = sum(row_cogs(r) for r in rows)
    gp = revenue_net - cogs

    category_breakdown = {}
    if any((r.get("Category") or "").strip() for r in rows):
        for r in rows:
            cat = (r.get("Category") or "Uncategorised").strip()
            category_breakdown.setdefault(cat, {"rev": 0.0, "cogs": 0.0, "qty": 0.0})
            category_breakdown[cat]["rev"] += row_rev(r)
            category_breakdown[cat]["cogs"] += row_cogs(r)
            category_breakdown[cat]["qty"] += parse_num(col(r, "Qty", "Product Quantity", "Quantity"))

    # ---- Product breakdown, with OUR cost where we have a recipe ----
    #
    # 'cost' has always been read verbatim from the Insights CSV, i.e. whatever
    # Lightspeed computed as (Produce recipe x Average Cost Price). Measured
    # 2026-07-16 on Stowaway: 11 products report $0.00 cost -- $1,530 of
    # $33,460 revenue (4.6%) booked at 100% GP because LS has no recipe. They
    # are all food; the food menu changed supplier and the recipes never
    # followed. Jalapeno Marg reports 96.6% GP.
    #
    # So: use our own cost where we have a recipe, keep LS's where we don't,
    # and ALWAYS emit both so they can be compared rather than trusted.
    # See COGS_ARCHITECTURE.md.
    our_costs = _load_our_costs(venue_key, target)

    product_breakdown = []
    for r in rows:
        name = (r.get("Product Name") or r.get("Product") or "").strip()
        if not name:
            continue
        qty = parse_num(col(r, "Product Quantity", "Qty", "Quantity"))
        ls_cost = row_cogs(r)
        entry = {
            "name": name,
            "qty": qty,
            "rev": row_rev(r),
            "cost": ls_cost,
            "cost_source": "lightspeed",
        }
        ours = our_costs.get(name)
        if ours is not None:
            entry["cost"] = round(ours * qty, 4)      # per-serve x units sold
            entry["cost_source"] = "recipe"
            entry["cost_lightspeed"] = ls_cost        # keep LS as a second opinion
        product_breakdown.append(entry)
    product_breakdown.sort(key=lambda p: p["rev"], reverse=True)

    # ---- Kitchen / FOH split (Stow + HG only) ----
    # 'f' + inbound cross rows = Kitchen slice; 'b' = FOH slice (catch-all).
    # 'm'/'hgf'/'stf' outbound rows are tracked for reconciliation only.
    dept_sums = {k: {"rev": 0.0, "cogs": 0.0} for k in ("f", "b", "m", "hgf", "stf")}
    if split_venue:
        for r, d in zip(all_rows, row_depts):
            dept_sums[d]["rev"] += row_rev(r)
            dept_sums[d]["cogs"] += row_cogs(r)
        for r in cross_rows:      # inbound reallocated rows are Kitchen/food
            dept_sums["f"]["rev"] += row_rev(r)
            dept_sums["f"]["cogs"] += row_cogs(r)
        # outbound tags don't belong in this venue's slices
        excl_tags = {"stowaway": {"m", "hgf"}, "harry": {"stf"}}[venue_key]
        for t in excl_tags:
            pass   # kept in dept_sums[t] for the record; not in f/b

    uber_eats_rev = 0
    for r in rows:
        pay_type = (r.get("PaymentType") or r.get("Payment Type") or "").lower()
        if "uber" in pay_type:
            uber_eats_rev += row_rev(r)

    lightspeed_data = {
        "revenue_inc": revenue_inc,
        "revenue_ex": revenue_net,
        "cogs": cogs,
        "gp": gp,
        "gp_pct": gp / revenue_net * 100 if revenue_net else 0,
        "cogs_pct": cogs / revenue_net * 100 if revenue_net else 0,
        "uber_eats_rev": uber_eats_rev,
        "category_breakdown": category_breakdown,
        "product_breakdown": product_breakdown[:20],
        "dept_sums": dept_sums if split_venue else None,
    }

# --------------------------------------------------------------
# Load payments CSV (Insights "Sales by Payment Type") — optional.
# --------------------------------------------------------------
payments_file = resolve(DATA_DIR / f"payments_{prefix}_{target.isoformat()}.csv")
payments_breakdown = None
if payments_file is not None:
    pay_text = read_insights_csv_text(payments_file)
    pay_reader = csv.DictReader(io.StringIO(pay_text))
    pay_rows = list(pay_reader)
    type_col = None
    for c in (pay_reader.fieldnames or []):
        if "payment" in c.lower() or "tender" in c.lower():
            type_col = c
            break
    payments_breakdown = {}
    uber_from_payments = 0.0
    for r in pay_rows:
        ptype = (r.get(type_col) or "Unknown").strip() if type_col else "Unknown"
        amt = parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales", "Sale Amount", "Total Sales", "Amount", "Total"))
        payments_breakdown[ptype] = payments_breakdown.get(ptype, 0.0) + amt
        if "uber" in ptype.lower():
            uber_from_payments += amt
    if lightspeed_data is not None:
        lightspeed_data["uber_eats_rev"] = uber_from_payments
        print(f"  Payments CSV: Uber Eats ${uber_from_payments:.2f} across {len(pay_rows)} rows")

# --------------------------------------------------------------
# Load manual Uber Direct weekly entry (Mari only) — optional.
# --------------------------------------------------------------
uber_direct_dollars = 0.0
uber_direct_file = DATA_DIR / "manual" / "uber_direct.json"
if "delivery" in lanes and uber_direct_file.exists():
    try:
        with uber_direct_file.open() as f:
            ud = json.load(f)
        week_ending = target + timedelta(days=(6 - target.weekday()))  # Sunday of target's week
        weekly_total = parse_num(ud.get("weeks", {}).get(week_ending.isoformat()))
        if weekly_total:
            uber_direct_dollars = weekly_total / 7.0
            print(f"  Uber Direct: ${weekly_total:.2f} for week ending {week_ending} -> ${uber_direct_dollars:.2f}/day")
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"  WARNING: could not parse {uber_direct_file}: {e}")

# --------------------------------------------------------------
# Load Deputy JSON. Costs are ex-super — gross up by SUPER_MULT so every
# reported figure is inc-super (weekly canon: TotalWagesIncSuper).
# --------------------------------------------------------------
deputy_file = resolve(
    DATA_DIR / f"deputy_{prefix}_{target.isoformat()}.json",
    DATA_DIR / f"deputy_{target.isoformat()}.json" if venue_key == "marilynas" else Path("/nonexistent"),
)

if deputy_file is None:
    deputy_data = None
else:
    with deputy_file.open() as f:
        d = json.load(f)

    # Super, PER PERSON — not a flat 12% (2026-07-18).
    #
    # This runs before Xero has posted the week, so it can't use actuals. But it
    # can use each person's OWN trailing rate, and that matters: Mari's drivers
    # are under 18 and legally get NO super, so 12% invented cost on exactly the
    # venue least able to carry it. Grossing flat here also meant Zak's 9am
    # number and the same week after the 6:30am rebuild disagreed by ~0.8% for
    # no reason he could see.
    #
    # Rules live in wage_model.super_lookup — shared with rebuild_wages and
    # roster_pull. Three copies of the gross-up is how it drifted to flat.
    _xp = DATA_DIR / "xero_pay_weekly.json"
    _xs = DATA_DIR / "xero_super_weekly.json"
    _em = DATA_DIR / "employee_map.json"
    if _xp.exists() and _xs.exists() and _em.exists():
        _super_for = super_lookup(json.loads(_xp.read_text()), json.loads(_xs.read_text()),
                                  json.loads(_em.read_text()), V.SUPER_RATE)
    else:
        print(f"  super: no Xero data — flat {V.SUPER_RATE * 100:.0f}% "
              f"(overstates; rebuild_wages corrects it at 6:30am)")
        _super_for = lambda _e, _w: SUPER_MULT
    _wk = (target - timedelta(days=target.weekday()) + timedelta(days=6)).isoformat()

    # Per-person calibration, learned by rebuild_wages from CLOSED weeks.
    #
    # Backtested 2026-07-18 over 13 weeks: the uncalibrated estimate was UNDER
    # what payroll actually paid in 357 of 398 employee-weeks — -4% overall,
    # -7.6% on hourly staff, only 2 weeks in 13 within +/-2%. Deputy's rates are
    # stale in a different way for each person (award rises, loading, penalties,
    # overtime, allowances), and modelling each cause is a losing game.
    #
    # So this figure carries each person's own measured error forward. It is a
    # correction learned from payslips, not a fudge factor.
    _cal_f = DATA_DIR / "wage_calibration.json"
    _cal = json.loads(_cal_f.read_text()) if _cal_f.exists() else {}
    if _cal:
        print(f"  wages: calibrated from {len(_cal)} people's closed weeks")
    else:
        print("  wages: NO calibration file — this number runs ~4% light. "
              "Run the full rebuild_wages --write to publish one.")

    def _rate(t):
        c = _cal.get(str(t.get("employee_id")))
        return _super_for(t.get("employee_id"), _wk) * (c["factor"] if c else 1.0)

    # UNAPPROVED TIMESHEETS — the single biggest error in this number.
    #
    # Deputy costs a shift when it is APPROVED. Yesterday's shifts usually
    # aren't. Measured 2026-07-18: 17 Jul had 13.50h of 93h (14.5%) at Cost = 0
    # — real hours, worked, that will absolutely be paid, booked at nothing.
    # The calibration factor cannot touch this: 0 x 1.05 is still 0.
    #
    # So cost them at the person's own learned $/h (published by rebuild_wages,
    # which sees enough Deputy history to know it; this script sees one day).
    # Marked _imputed so it's visible rather than silently blended in.
    _imp_n = _imp_h = 0

    def _cost_of(t):
        global _imp_n, _imp_h
        c = t.get("cost") or 0
        h = t.get("hours") or 0
        if c == 0 and h > 0 and not t.get("salaried_synth"):
            r = (_cal.get(str(t.get("employee_id"))) or {}).get("rate_per_hour")
            if r:
                _imp_n += 1
                _imp_h += h
                return h * r
        return c

    def dept_cost(name):
        return sum(_cost_of(t) * _rate(t) for t in d if t.get("dept") == name)
    kitchen_cost = dept_cost("Kitchen")
    foh_cost = dept_cost("FOH")
    driver_cost = dept_cost("Driver")
    admin_cost = dept_cost("Admin")
    # Leave is group overhead — NOT in the venue total (weekly canon); the
    # dashboard adds it to the synthesized Group wage figure.
    leave_cost = dept_cost("Leave")
    if _imp_n:
        print(f"  wages: {_imp_n} unapproved shift(s) ({_imp_h:.2f}h) costed at "
              f"the person's own rate — Deputy has no cost until approval")
    _unratable = [t for t in d if (t.get("cost") or 0) == 0 and (t.get("hours") or 0) > 0
                  and not t.get("salaried_synth")
                  and not (_cal.get(str(t.get("employee_id"))) or {}).get("rate_per_hour")]
    if _unratable:
        # No rate, no imputation. These hours book $0 and the number is light by
        # however much they're worth. Say so — a silent $0 is how a wage line
        # goes quietly wrong.
        print(f"  !! wages: {len(_unratable)} shift(s), "
              f"{sum(t.get('hours') or 0 for t in _unratable):.2f}h — real hours, no cost, "
              f"and no known rate to impute from. BOOKED AT $0:")
        for t in _unratable[:5]:
            print(f"       employee {t.get('employee_id')} ({t.get('employee_name')}) "
                  f"{t.get('hours')}h {t.get('dept')}")
    # Total = Kitchen + FOH + Admin + Driver. Driver stays inside the venue
    # total (Mari Venue Total = Kitchen + Driver in the weekly canon) AND
    # also surfaces in the delivery lane.
    total_wages = kitchen_cost + foh_cost + driver_cost + admin_cost
    deputy_data = {
        "kitchen_wages": kitchen_cost,
        "foh_wages": foh_cost,
        "driver_wages": driver_cost,
        "admin_wages": admin_cost,
        "leave_wages": leave_cost,
        "total_wages": total_wages,
        "kitchen_hours": sum(t.get("hours", 0) for t in d if t.get("dept") == "Kitchen"),
        "foh_hours":     sum(t.get("hours", 0) for t in d if t.get("dept") == "FOH"),
        "driver_hours":  sum(t.get("hours", 0) for t in d if t.get("dept") == "Driver"),
        "admin_hours":   sum(t.get("hours", 0) for t in d if t.get("dept") == "Admin"),
    }

# --------------------------------------------------------------
# Compute lanes
# --------------------------------------------------------------
if lightspeed_data and lightspeed_data.get("uber_eats_rev"):
    uber_commission = lightspeed_data["uber_eats_rev"] / 1.1 * UBER_COMMISSION_RATE
else:
    uber_commission = 0

driver_dollars = deputy_data["driver_wages"] if deputy_data else 0

if lightspeed_data:
    rev_ex = lightspeed_data["revenue_ex"]
    cogs_dollars = lightspeed_data["cogs"]
    cogs_pct = cogs_dollars / rev_ex * 100 if rev_ex else 0
    if deputy_data:
        wages_dollars = deputy_data["total_wages"]
        wages_pct = wages_dollars / rev_ex * 100 if rev_ex else 0
    else:
        wages_dollars = wages_pct = None
    delivery_dollars = driver_dollars + uber_commission + uber_direct_dollars
    delivery_pct = delivery_dollars / rev_ex * 100 if rev_ex else 0
else:
    rev_ex = cogs_dollars = cogs_pct = None
    wages_dollars = wages_pct = None
    delivery_dollars = delivery_pct = None

# ---- Marilynas Net Wage % (weekly canon: net of Uber fees) ----
# Real fee data (service + marketing + amendments) comes from the Uber
# merchant portal weekly; daily we only have the 30% commission estimate +
# amortized Uber Direct. Flagged as estimate in the record.
net_takings_ex = net_wage_pct = None
if venue_key == "marilynas" and rev_ex:
    uber_fees_est = uber_commission + uber_direct_dollars
    if uber_fees_est:
        net_takings_ex = rev_ex - uber_fees_est
        if wages_dollars is not None and net_takings_ex:
            net_wage_pct = wages_dollars / net_takings_ex * 100

# ---- Split lane figures ----
split = None
if lightspeed_data and split_venue and lightspeed_data.get("dept_sums"):
    ds = lightspeed_data["dept_sums"]
    food_ex = ds["f"]["rev"] / 1.1
    bev_ex = ds["b"]["rev"] / 1.1
    food_cogs = ds["f"]["cogs"]
    bev_cogs = ds["b"]["cogs"]
    split = {
        "food_ex_gst": round(food_ex, 2),
        "bev_ex_gst": round(bev_ex, 2),
        "food_cogs": round(food_cogs, 2),
        "bev_cogs": round(bev_cogs, 2),
        "food_cogs_pct": round(food_cogs / food_ex * 100, 1) if food_ex else None,
        "bev_cogs_pct": round(bev_cogs / bev_ex * 100, 1) if bev_ex else None,
        "food_gp_pct": round((food_ex - food_cogs) / food_ex * 100, 1) if food_ex else None,
        "bev_gp_pct": round((bev_ex - bev_cogs) / bev_ex * 100, 1) if bev_ex else None,
        "mari_rideon_ex_gst": round(ds["m"]["rev"] / 1.1, 2),
        "hg_food_out_ex_gst": round(ds["hgf"]["rev"] / 1.1, 2),
        "stow_food_out_ex_gst": round(ds["stf"]["rev"] / 1.1, 2),
    }
# Marilyna's is a pizza shop: 100% food, and the only non-kitchen labour is the
# Driver OU (which Deputy tags separately, so it never lands in kitchen_wages).
# There's no split to CLASSIFY here — but the columns aren't unknowable, they're
# trivially true: revenue IS food revenue, COGS IS food COGS, bev is zero.
# Leaving them blank made Mari's own venue tab say "awaiting split data" for
# numbers we've had since Oct 2024, while the Big Chef group view derived the
# same thing itself. Emit them properly instead (Zak, 2026-07-15).
elif venue_key == "marilynas" and lightspeed_data:
    food_ex = lightspeed_data["revenue_ex"]
    food_cogs_m = lightspeed_data["cogs"]
    split = {
        "food_ex_gst": round(food_ex, 2),
        "bev_ex_gst": 0.0,
        "food_cogs": round(food_cogs_m, 2),
        "bev_cogs": 0.0,
        "food_cogs_pct": round(food_cogs_m / food_ex * 100, 1) if food_ex else None,
        "bev_cogs_pct": None,
        "food_gp_pct": round((food_ex - food_cogs_m) / food_ex * 100, 1) if food_ex else None,
        "bev_gp_pct": None,
    }

wages_kitchen_pct = wages_foh_pct = None
if deputy_data and split:
    if split["food_ex_gst"]:
        wages_kitchen_pct = round(deputy_data["kitchen_wages"] / split["food_ex_gst"] * 100, 1)
    if split["bev_ex_gst"]:
        wages_foh_pct = round(deputy_data["foh_wages"] / split["bev_ex_gst"] * 100, 1)

# Baseline / targets
baseline_path = BASELINES_DIR / cfg["baseline_file"]
if baseline_path.exists():
    with baseline_path.open() as f:
        baseline = json.load(f)
    targets = baseline["targets_and_alerts"]
else:
    print(f"WARNING: baseline {baseline_path} missing — using empty targets")
    targets = {}


def status(v, c):
    if v is None or c is None: return "unknown"
    if v >= c.get("red",   float("inf")): return "red"
    if v >= c.get("amber", float("inf")): return "amber"
    if v <= c.get("target", float("inf")): return "green"
    return "yellow"


cogs_status = status(cogs_pct, targets.get("cogs"))
wages_status = status(wages_pct, targets.get("wages"))
delivery_status = status(delivery_pct, targets.get("delivery")) if "delivery" in lanes else "n/a"
gp_status = status(lightspeed_data["gp_pct"] if lightspeed_data else None, targets.get("gp"))
cogs_food_status = status(split["food_cogs_pct"] if split else None, targets.get("cogs_food"))
cogs_bev_status = status(split["bev_cogs_pct"] if split else None, targets.get("cogs_bev"))
wages_kitchen_status = status(wages_kitchen_pct, targets.get("wages_kitchen"))
wages_foh_status = status(wages_foh_pct, targets.get("wages_foh"))

# --------------------------------------------------------------
# Record
# --------------------------------------------------------------
record = {
    "date": target.isoformat(),
    "generated_at": datetime.utcnow().isoformat() + "Z",
    "venue": venue_key,
    "venue_display": cfg["display_name"],
    "lane_config": cfg["lane_config"],
    "data_status": {
        "lightspeed": "ok" if lightspeed_data else "missing",
        "deputy":     "ok" if deputy_data else "missing",
        "payments":   "ok" if payments_breakdown is not None else "missing",
    },
    "sales": {
        "revenue_inc_gst": round(lightspeed_data["revenue_inc"], 2) if lightspeed_data else None,
        "revenue_ex_gst":  round(rev_ex, 2) if rev_ex else None,
        "cogs_dollars":    round(cogs_dollars, 2) if cogs_dollars is not None else None,
        "cogs_pct":        round(cogs_pct, 1) if cogs_pct is not None else None,
        "gp_dollars":      round(lightspeed_data["gp"], 2) if lightspeed_data else None,
        "gp_pct":          round(lightspeed_data["gp_pct"], 1) if lightspeed_data else None,
        "uber_eats_revenue": round(lightspeed_data.get("uber_eats_rev", 0), 2) if lightspeed_data else 0,
        "net_takings_ex_gst": round(net_takings_ex, 2) if net_takings_ex is not None else None,
        **(split or {}),
    },
    "wages": {
        "kitchen_dollars": round(deputy_data["kitchen_wages"], 2) if deputy_data else None,
        "foh_dollars":     round(deputy_data.get("foh_wages", 0), 2) if deputy_data else None,
        "driver_dollars":  round(deputy_data["driver_wages"], 2) if deputy_data else None,
        "admin_dollars":   round(deputy_data.get("admin_wages", 0), 2) if deputy_data else None,
        "leave_dollars":   round(deputy_data.get("leave_wages", 0), 2) if deputy_data else None,
        "total_dollars":   round(wages_dollars, 2) if wages_dollars is not None else None,
        "wages_pct":       round(wages_pct, 1) if wages_pct is not None else None,
        "net_wage_pct":    round(net_wage_pct, 1) if net_wage_pct is not None else None,
        "kitchen_hours":   round(deputy_data.get("kitchen_hours", 0), 1) if deputy_data else None,
        "foh_hours":       round(deputy_data.get("foh_hours", 0), 1) if deputy_data else None,
        "wages_kitchen_pct": wages_kitchen_pct,
        "wages_foh_pct":     wages_foh_pct,
        "includes_super": True,
        "salaried_synthesized": True,
    },
    "delivery": {
        "uber_eats_commission_dollars": round(uber_commission, 2),
        "own_driver_dollars":           round(driver_dollars, 2),
        "uber_direct_dollars":          round(uber_direct_dollars, 2),
        "total_dollars":                round(delivery_dollars, 2) if delivery_dollars is not None else None,
        "delivery_pct":                 round(delivery_pct, 1) if delivery_pct is not None else None,
        "fees_are_estimate":            True,
    } if "delivery" in lanes else None,
    "payments_breakdown": {k: round(v, 2) for k, v in payments_breakdown.items()} if payments_breakdown else None,
    "alerts": {
        "cogs":     cogs_status,
        "wages":    wages_status,
        "delivery": delivery_status,
        "gp":       gp_status,
        "cogs_food":     cogs_food_status,
        "cogs_bev":      cogs_bev_status,
        "wages_kitchen": wages_kitchen_status,
        "wages_foh":     wages_foh_status,
    },
    "targets": targets,
    "top_products": lightspeed_data.get("product_breakdown", []) if lightspeed_data else [],
}

# Write venue-prefixed record
out_file = DATA_DIR / f"{prefix}_daily_{target.isoformat()}.json"
with out_file.open("w") as f:
    json.dump(record, f, indent=2)
print(f"Saved {out_file}")

# --------------------------------------------------------------
# Append to history CSV (FULL history — no trailing-window trim; the
# backfill from the product masters lives in these files)
# --------------------------------------------------------------
history_file = DATA_DIR / f"{prefix}_daily_history.csv"
history_rows = []
if history_file.exists():
    with history_file.open() as f:
        history_rows = list(csv.DictReader(f))
# Keep the row we're about to replace: it carries fields this script doesn't own
# (rebuild_wages writes the assumed pass from the roster, which we never fetch)
# and history is rewritten from nr.keys(), so anything not carried is deleted.
prev_row = next((r for r in history_rows if r["date"] == target.isoformat()), None)
history_rows = [r for r in history_rows if r["date"] != target.isoformat()]

nr = {
    "date": target.isoformat(),
    "revenue_ex_gst":   record["sales"]["revenue_ex_gst"],
    "cogs_dollars":     record["sales"]["cogs_dollars"],
    "cogs_pct":         record["sales"]["cogs_pct"],
    "wages_dollars":    record["wages"]["total_dollars"],
    "wages_pct":        record["wages"]["wages_pct"],
    "delivery_dollars": record["delivery"]["total_dollars"] if record["delivery"] else "",
    "delivery_pct":     record["delivery"]["delivery_pct"] if record["delivery"] else "",
    "gp_dollars":       record["sales"]["gp_dollars"],
    "gp_pct":           record["sales"]["gp_pct"],
    "cogs_alert":       cogs_status,
    "wages_alert":      wages_status,
    "delivery_alert":   delivery_status,
    "gp_alert":         gp_status,
    "food_ex_gst":            split["food_ex_gst"] if split else "",
    "bev_ex_gst":             split["bev_ex_gst"] if split else "",
    "food_cogs":              split["food_cogs"] if split else "",
    "bev_cogs":               split["bev_cogs"] if split else "",
    "food_cogs_pct":          (split["food_cogs_pct"] if split and split["food_cogs_pct"] is not None else ""),
    "bev_cogs_pct":           (split["bev_cogs_pct"] if split and split["bev_cogs_pct"] is not None else ""),
    "food_gp_pct":            (split["food_gp_pct"] if split and split["food_gp_pct"] is not None else ""),
    "bev_gp_pct":             (split["bev_gp_pct"] if split and split["bev_gp_pct"] is not None else ""),
    # Emitted for EVERY venue, not just the split ones. These are computed from
    # Deputy's own OU tagging for all three venues — the old `split_venue` gate
    # threw Mari's away at write time even though the pull had already worked it
    # out. Driver gets its own column: it's real labour, it is NOT kitchen, and
    # it must not be inferred from delivery_dollars (which also carries Uber
    # commission and Uber Direct fees).
    "wages_kitchen_dollars":  record["wages"]["kitchen_dollars"] if deputy_data else "",
    "wages_foh_dollars":      record["wages"]["foh_dollars"] if deputy_data else "",
    "wages_driver_dollars":   record["wages"]["driver_dollars"] if deputy_data else "",
    # Admin is inside wages_dollars but is NOT cost the venue can roster against,
    # so venue views strip it (2026-07-17). It has to be written here as well as
    # in rebuild_wages: the aggregator rewrites these rows every morning, and a
    # column it doesn't emit gets blanked — the venue split would silently fall
    # back to total-minus-parts and quietly re-absorb admin.
    "wages_admin_dollars":    record["wages"]["admin_dollars"] if deputy_data else "",
    # The assumed first pass belongs to rebuild_wages — it needs the ROSTER, which
    # this script never fetches. But history is rewritten here every morning from
    # nr.keys(), so a column this dict doesn't name is DELETED from the CSV. Carry
    # the existing value through untouched: rebuild_wages runs after the pull
    # (7:15am, and again at 12:10pm) and refills it. Without these two lines the
    # 6am pull silently drops the column and the card falls back to the raw,
    # half-clocked number with nothing saying so — the exact 14.7% problem the
    # assumed pass exists to solve.
    "wages_assumed_dollars":  (prev_row or {}).get("wages_assumed_dollars", ""),
    "wages_assumed_shifts":   (prev_row or {}).get("wages_assumed_shifts", ""),
    "wages_kitchen_pct":      wages_kitchen_pct if wages_kitchen_pct is not None else "",
    "wages_foh_pct":          wages_foh_pct if wages_foh_pct is not None else "",
    "cogs_food_alert":        cogs_food_status,
    "cogs_bev_alert":         cogs_bev_status,
    "wages_kitchen_alert":    wages_kitchen_status,
    "wages_foh_alert":        wages_foh_status,
    "uber_eats_revenue":      record["sales"]["uber_eats_revenue"],
    "uber_direct_dollars":    round(uber_direct_dollars, 2) if "delivery" in lanes else "",
    "leave_dollars":          (record["wages"]["leave_dollars"] if deputy_data and venue_key == "stowaway" else ""),
}
history_rows.append(nr)
history_rows.sort(key=lambda r: r["date"])
fieldnames = list(nr.keys())
with history_file.open("w", newline="") as f:
    if history_rows:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in history_rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
print(f"History: {len(history_rows)} rows -> {history_file}")
