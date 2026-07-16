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

sys.path.insert(0, str(Path(__file__).parent))
import venues as V

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
    "delivery alcohol", "delivery cocktails",
    # 'delivery kitchen' REMOVED 2026-05-13 — it's Stow Kitchen food on Uber.
}
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
insights_file = resolve(
    DATA_DIR / f"insights_{prefix}_{target.isoformat()}.csv",
    DATA_DIR / f"insights_{target.isoformat()}.csv" if venue_key == "marilynas" else Path("/nonexistent"),
)

if insights_file is None:
    print(f"Insights CSV not found for {venue_key} {target.isoformat()}")
    print("Will emit alert-only record with 'data_missing' flag")
    lightspeed_data = None
else:
    all_rows, fieldnames = load_product_rows(insights_file)
    print(f"  Parsed {len(all_rows)} rows; columns: {fieldnames}")

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

    rows = rows + cross_rows

    # ---- Mari coverage guard (2026-07-16) ----
    # Marilyna's has no till of its own — it's a brand running through the Stow
    # POS, and its "own" CSV is just a FILTERED EXTRACT of that same POS. Stow
    # strips every 'm' row from its totals (line ~302) on the assumption Mari's
    # report picks them up. Nothing ever checked that assumption.
    #
    # On 2026-07-14 the 'Mari Daily Sales Auto' report filter changed and
    # 'Dine-in Pizza' fell out of it. Those rows were still stripped off Stow and
    # were no longer in Mari's report, so the revenue left the group entirely —
    # $612.70 ex on the 14th, $235.71 on the 15th, silently. Before the 14th the
    # two matched to the cent, which is why nobody saw it.
    #
    # This can't be fixed by pulling the rows in: ~54% of them ARE already in
    # Mari's file, so that double-counts. The fix is the report filter. This is
    # the tripwire that catches the next filter change in a day, not a quarter.
    if venue_key == "marilynas":
        _sib = resolve(DATA_DIR / f"insights_stow_{target.isoformat()}.csv")
        if _sib is not None:
            _sib_rows, _ = load_product_rows(_sib)
            _own = {(r.get("Product Name") or r.get("Product") or "").strip() for r in rows}
            _lost = [r for r in _sib_rows
                     if classify_product(r, (r.get("Product Name") or r.get("Product") or "").strip(), "stowaway") == 'm'
                     and (r.get("Product Name") or r.get("Product") or "").strip() not in _own]
            if _lost:
                _amt = sum(row_rev(r) for r in _lost)
                print(f"  *** REVENUE LEAK: {len(_lost)} Mari rows on the Stow till (${_amt:,.2f} inc) are NOT in")
                print(f"      Mari's report. Stow strips them, Mari never receives them -> they reach NO venue.")
                print(f"      Fix the 'Mari Daily Sales Auto' report filter in Lightspeed to include them.")
                for r in _lost[:6]:
                    print(f"        {(r.get('Product Name') or '').strip()[:46]}  ${row_rev(r):,.2f}")
                if len(_lost) > 6:
                    print(f"        ... and {len(_lost)-6} more")

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

    product_breakdown = []
    for r in rows:
        name = (r.get("Product Name") or r.get("Product") or "").strip()
        if not name:
            continue
        product_breakdown.append({
            "name": name,
            "qty": parse_num(col(r, "Product Quantity", "Qty", "Quantity")),
            "rev": row_rev(r),
            "cost": row_cogs(r),
        })
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
    def dept_cost(name):
        return sum(t["cost"] for t in d if t.get("dept") == name) * SUPER_MULT
    kitchen_cost = dept_cost("Kitchen")
    foh_cost = dept_cost("FOH")
    driver_cost = dept_cost("Driver")
    admin_cost = dept_cost("Admin")
    leave_cost = dept_cost("Leave")   # Group overhead — NOT in the venue total
                                      # (weekly canon); the dashboard adds it to
                                      # the synthesized Group wage figure.
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
