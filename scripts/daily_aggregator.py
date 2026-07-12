"""
Daily aggregator — runs each morning after Insights CSV lands.

Inputs (per venue):
  - data/insights_<prefix>_<yyyy-mm-dd>.csv   (Lightspeed Insights daily report;
                                                may be a ZIP wrapping the CSV;
                                                supports Sales-Summary and
                                                Sales-by-Product schemas)
  - data/deputy_<prefix>_<yyyy-mm-dd>.json    (Deputy API daily wages)
  - data/payments_<prefix>_<yyyy-mm-dd>.csv   (OPTIONAL — Insights Sales by
                                                Payment Type; gives real Uber
                                                Eats revenue. Until the
                                                schedule exists, Uber falls
                                                back to $0 / product-CSV scan.)
  - data/manual/uber_direct.json              (OPTIONAL — Zak-entered weekly
                                                Uber Direct fee totals,
                                                amortized across the week.)

  Marilynas falls back to unprefixed insights_<date>.csv / deputy_<date>.json
  for backwards compat with the existing daily_pull workflow.

Kitchen / FOH split:
  Products are classified food ('f') / bev ('b') / Marilynas ride-on ('m') /
  other ('o') via scripts/product_dept_map.json — generated from the
  weekly-report skill's canonical reporting_group_mapping.csv + the
  ReportingGroup -> Department table. DO NOT hand-edit keyword rules here;
  regenerate the map from the weekly-report skill so daily and weekly
  reporting stay consistent. If the CSV carries a Category / Reporting Group
  column it wins over the product-name lookup.

  Marilynas ride-on rows ('m') are EXCLUDED from Stow/HG venue totals —
  they're Marilynas P&L and arrive via Mari's own schedule. They're surfaced
  as sales.mari_rideon_ex_gst for reconciliation.

Output:
  - data/<prefix>_daily_<yyyy-mm-dd>.json   (per-day rollup with alerts)
  - data/<prefix>_daily_history.csv         (90-day trailing)

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
    """Parse a Kounta-Insights currency/number cell.

    Handles '$1,234.56', '', None, '(45.00)' → -45.00, percentages like
    '12.5%' → 12.5.
    """
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
# Product -> department classification (canonical, from weekly-report skill)
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

# Reporting-Group-level fallback when the CSV carries a category column.
# Mirrors the canonical table; keyword-free.
_RG_DEPT = None

def _rg_dept(rg: str) -> str:
    global _RG_DEPT
    if _RG_DEPT is None:
        bev = {'tap beer','cocktails - classic','cocktails - signature','red wine','white wine','rose wine',
               'orange / skins wine','sparkling wine','pet nat wine','bottles / cans alcoholic','packaged beer',
               'bottled beer','wine - by the glass','wine - by the bottle','non-alcoholic','mocktails',
               'add-ons - bar','functions & misc.','soft drinks','gin','gins','vodka','vodkas','whisky',
               'whiskies','tequila','tequilas','rum','rums','mezcal','brandy','liqueurs','other spirits',
               'amaro / aperitif / fortified wine','sake & soju','spirits'}
        food = {'big plates','small plates','kitchen specials','salads','desserts','kids meals','kids',
                'add-ons - kitchen','harry gatos food','delivery kitchen','stow food','sides','snacks',
                'yum cha','staff dinners'}
        mari = {"marilyna's pizza",'marilynas pizza','dine-in pizza','add-ons - pizza',
                "marilyna's soft drinks",'marilynas soft drinks','delivery alcohol','delivery cocktails'}
        _RG_DEPT = {}
        for g in bev: _RG_DEPT[g] = 'b'
        for g in food: _RG_DEPT[g] = 'f'
        for g in mari: _RG_DEPT[g] = 'm'
    key = (rg or '').strip().lower()
    if key.endswith(' [harrys]'):
        key = key[:-len(' [harrys]')]
    return _RG_DEPT.get(key, 'o')


def classify_product(row: dict, product_name: str, venue_key: str) -> str:
    """-> 'f' (food/Kitchen), 'b' (bev/FOH), 'm' (Marilynas ride-on), 'o' (other).

    Prefers an explicit Reporting Group / Category column when the CSV has
    one; otherwise resolves product name through the canonical map.
    """
    rg = col(row, "Reporting Group Name", "Reporting Group", "Category")
    if rg:
        d = _rg_dept(rg)
        if d != 'o':
            return d
    m = _load_dept_map()
    vkey = {"stowaway": "stow", "harry": "hg"}.get(venue_key)
    if vkey is None:
        return 'o'
    pn = (product_name or '').strip()
    return m.get(vkey, {}).get(pn) or m.get("*", {}).get(pn) or 'o'


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
    """First existing path from a list, else None."""
    for c in candidates:
        if c.exists():
            return c
    return None


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
    csv_text = read_insights_csv_text(insights_file)
    reader = csv.DictReader(io.StringIO(csv_text))
    all_rows = list(reader)
    print(f"  Parsed {len(all_rows)} rows; columns: {reader.fieldnames}")

    # ---- Marilynas ride-on exclusion (Stow only) ----
    # The Stow Insights schedule is site-filtered but NOT reporting-group
    # filtered, so Marilynas products (rung through the Stowaway POS) appear
    # in the CSV. They are Marilynas P&L — Mari has its own schedule + CSV —
    # so counting them here would double-count the Group rollup. Classify
    # every row once; 'm' rows are excluded from ALL venue totals below and
    # surfaced separately as mari_rideon_ex_gst.
    if split_venue:
        row_depts = [
            classify_product(r, (r.get("Product Name") or r.get("Product") or "").strip(), venue_key)
            for r in all_rows
        ]
        rows = [r for r, d in zip(all_rows, row_depts) if d != "m"]
        excluded = len(all_rows) - len(rows)
        if excluded:
            print(f"  Excluded {excluded} Marilynas ride-on rows from {venue_key} totals")
    else:
        row_depts = None
        rows = all_rows

    revenue_inc = sum(
        parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales", "Sale Amount", "Total Sales"))
        for r in rows
    )
    total_tax = sum(parse_num(col(r, "Total Tax", "GST", "Tax")) for r in rows)
    revenue_net_explicit = sum(
        parse_num(col(r, "Revenue_net", "NetRevenue", "Net Sales")) for r in rows
    )
    if revenue_net_explicit > 0:
        revenue_net = revenue_net_explicit
    elif total_tax > 0:
        revenue_net = revenue_inc - total_tax
    else:
        revenue_net = revenue_inc / 1.1

    cogs = sum(parse_num(col(r, "COGS", "Cost", "Cost of Goods Sold")) for r in rows)
    gp = revenue_net - cogs

    category_breakdown = {}
    if any((r.get("Category") or "").strip() for r in rows):
        for r in rows:
            cat = (r.get("Category") or "Uncategorised").strip()
            category_breakdown.setdefault(cat, {"rev": 0.0, "cogs": 0.0, "qty": 0.0})
            category_breakdown[cat]["rev"] += parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales"))
            category_breakdown[cat]["cogs"] += parse_num(col(r, "COGS", "Cost"))
            category_breakdown[cat]["qty"] += parse_num(col(r, "Qty", "Product Quantity", "Quantity"))

    product_breakdown = []
    for r in rows:
        name = (r.get("Product Name") or r.get("Product") or "").strip()
        if not name:
            continue
        product_breakdown.append({
            "name": name,
            "qty": parse_num(col(r, "Product Quantity", "Qty", "Quantity")),
            "rev": parse_num(col(r, "$ Sales", "Sales", "Revenue_inc_gst")),
            "cost": parse_num(col(r, "Cost", "COGS")),
        })
    product_breakdown.sort(key=lambda p: p["rev"], reverse=True)

    # ---- Kitchen / FOH split (Stow + HG only) ----
    # Sums are inc-GST off the raw rows, then /1.1 to ex-GST per slice.
    # (Explicit per-slice tax isn't available; 10% GST applies to both slices.)
    # Uses ALL rows (incl. 'm') so mari_rideon_ex_gst stays visible even
    # though 'm' rows are excluded from the venue totals above.
    dept_sums = {k: {"rev": 0.0, "cogs": 0.0} for k in ("f", "b", "m", "o")}
    if split_venue:
        for r, d in zip(all_rows, row_depts):
            dept_sums[d]["rev"] += parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales", "Sale Amount", "Total Sales"))
            dept_sums[d]["cogs"] += parse_num(col(r, "COGS", "Cost", "Cost of Goods Sold"))
        unsplit = dept_sums["o"]["rev"]
        if revenue_inc and unsplit / revenue_inc > 0.10:
            print(f"  WARNING: {unsplit/revenue_inc*100:.1f}% of revenue unclassified (other) — "
                  f"product_dept_map.json may need regenerating from the weekly-report skill")

    uber_eats_rev = 0
    for r in rows:
        pay_type = (r.get("PaymentType") or r.get("Payment Type") or "").lower()
        if "uber" in pay_type:
            uber_eats_rev += parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales"))

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
# Gives real Uber Eats revenue; overrides the product-CSV scan above.
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
# data/manual/uber_direct.json: {"weeks": {"<week-ending Sunday ISO>": dollars}}
# Amortized evenly across the 7 days of that week.
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
# Load Deputy JSON
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
    kitchen_cost = sum(t["cost"] for t in d if t.get("dept") == "Kitchen")
    foh_cost = sum(t["cost"] for t in d if t.get("dept") == "FOH")
    driver_cost = sum(t["cost"] for t in d if t.get("dept") == "Driver")
    total_wages = kitchen_cost + foh_cost + driver_cost
    deputy_data = {
        "kitchen_wages": kitchen_cost,
        "foh_wages": foh_cost,
        "driver_wages": driver_cost,
        "total_wages": total_wages,
        "kitchen_hours": sum(t.get("hours", 0) for t in d if t.get("dept") == "Kitchen"),
        "foh_hours":     sum(t.get("hours", 0) for t in d if t.get("dept") == "FOH"),
        "driver_hours":  sum(t.get("hours", 0) for t in d if t.get("dept") == "Driver"),
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
        "other_ex_gst": round(ds["o"]["rev"] / 1.1, 2),
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
        **(split or {}),
    },
    "wages": {
        "kitchen_dollars": round(deputy_data["kitchen_wages"], 2) if deputy_data else None,
        "foh_dollars":     round(deputy_data.get("foh_wages", 0), 2) if deputy_data else None,
        "driver_dollars":  round(deputy_data["driver_wages"], 2) if deputy_data else None,
        "total_dollars":   round(wages_dollars, 2) if wages_dollars is not None else None,
        "wages_pct":       round(wages_pct, 1) if wages_pct is not None else None,
        "kitchen_hours":   round(deputy_data.get("kitchen_hours", 0), 1) if deputy_data else None,
        "foh_hours":       round(deputy_data.get("foh_hours", 0), 1) if deputy_data else None,
        "wages_kitchen_pct": wages_kitchen_pct,
        "wages_foh_pct":     wages_foh_pct,
    },
    "delivery": {
        "uber_eats_commission_dollars": round(uber_commission, 2),
        "own_driver_dollars":           round(driver_dollars, 2),
        "uber_direct_dollars":          round(uber_direct_dollars, 2),
        "total_dollars":                round(delivery_dollars, 2) if delivery_dollars is not None else None,
        "delivery_pct":                 round(delivery_pct, 1) if delivery_pct is not None else None,
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
# Append to history CSV
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
    # ---- split columns (empty for Mari / pre-split rows) ----
    "food_ex_gst":            split["food_ex_gst"] if split else "",
    "bev_ex_gst":             split["bev_ex_gst"] if split else "",
    "food_cogs":              split["food_cogs"] if split else "",
    "bev_cogs":               split["bev_cogs"] if split else "",
    "food_cogs_pct":          (split["food_cogs_pct"] if split and split["food_cogs_pct"] is not None else ""),
    "bev_cogs_pct":           (split["bev_cogs_pct"] if split and split["bev_cogs_pct"] is not None else ""),
    "food_gp_pct":            (split["food_gp_pct"] if split and split["food_gp_pct"] is not None else ""),
    "bev_gp_pct":             (split["bev_gp_pct"] if split and split["bev_gp_pct"] is not None else ""),
    "wages_kitchen_dollars":  record["wages"]["kitchen_dollars"] if deputy_data and split_venue else "",
    "wages_foh_dollars":      record["wages"]["foh_dollars"] if deputy_data and split_venue else "",
    "wages_kitchen_pct":      wages_kitchen_pct if wages_kitchen_pct is not None else "",
    "wages_foh_pct":          wages_foh_pct if wages_foh_pct is not None else "",
    "cogs_food_alert":        cogs_food_status,
    "cogs_bev_alert":         cogs_bev_status,
    "wages_kitchen_alert":    wages_kitchen_status,
    "wages_foh_alert":        wages_foh_status,
    # ---- Uber columns ----
    "uber_eats_revenue":      record["sales"]["uber_eats_revenue"],
    "uber_direct_dollars":    round(uber_direct_dollars, 2) if "delivery" in lanes else "",
}
history_rows.append(nr)
cutoff = target - timedelta(days=90)
history_rows = [r for r in history_rows if date.fromisoformat(r["date"]) > cutoff]
history_rows.sort(key=lambda r: r["date"])
fieldnames = list(nr.keys())
with history_file.open("w", newline="") as f:
    if history_rows:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in history_rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
print(f"History: {len(history_rows)} rows -> {history_file}")
