"""
Mari daily aggregator — runs each morning after Insights CSV lands.

Inputs:
  - data/insights_<yyyy-mm-dd>.csv   (Lightspeed Insights daily sales report
                                       — may be a ZIP wrapping the CSV; we
                                       auto-detect and unwrap. Supports both
                                       the Sales-Summary schema and the
                                       Sales-by-Product schema.)
  - data/deputy_<yyyy-mm-dd>.json    (Deputy API daily wages)

Output:
  - data/mari_daily_<yyyy-mm-dd>.json  (per-day 4-lane rollup with alerts)
  - data/mari_daily_history.csv        (append-only 60-day trailing)

Runs claude-less on GitHub Actions cron.
"""
import csv, io, json, os, sys, zipfile
from pathlib import Path
from datetime import date, timedelta, datetime

# On GitHub Actions runner, CWD is the repo checkout root.
REPO_ROOT = Path(os.environ.get("REPO_ROOT", "."))
DATA_DIR = REPO_ROOT / "data"
BASELINE = REPO_ROOT / "baselines" / "mari_baseline.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def read_insights_csv_text(path: Path) -> str:
    """Return CSV text from an Insights payload that may be a raw CSV or a ZIP.

    Lightspeed Insights scheduled emails deliver the report as a .zip
    attachment containing the CSV. We detect ZIP magic bytes (PK\\x03\\x04)
    and, if present, unzip in memory and return the largest .csv member
    (Insights summary files are smaller than the main data table).
    Otherwise read the file as plain CSV text.
    """
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

    Handles: '$1,234.56', '1234.56', '', None, '(45.00)' → -45.00,
    percentages like '12.5%' → 12.5, unquoted floats, ints.
    """
    if x is None:
        return 0.0
    s = str(x).strip()
    if not s:
        return 0.0
    # Parens = negative in accounting exports
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    # Strip currency symbols and thousand separators and percent
    s = s.replace("$", "").replace(",", "").replace("%", "").strip()
    if not s:
        return 0.0
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if negative else v


def col(r: dict, *candidates: str) -> str:
    """Return the first non-empty value from a row across candidate column names."""
    for c in candidates:
        v = r.get(c)
        if v not in (None, ""):
            return v
    return ""


# ==============================================================
# 1. Determine target date (yesterday by default, or CLI arg)
# ==============================================================
if len(sys.argv) > 1:
    target = date.fromisoformat(sys.argv[1])
else:
    target = date.today() - timedelta(days=1)

print(f"Aggregating for: {target.isoformat()}")

# ==============================================================
# 2. Load Lightspeed Insights CSV
# ==============================================================
insights_file = DATA_DIR / f"insights_{target.isoformat()}.csv"
if not insights_file.exists():
    print(f"Insights CSV not found: {insights_file}")
    print("Will emit alert-only record with 'data_missing' flag")
    lightspeed_data = None
else:
    csv_text = read_insights_csv_text(insights_file)
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    print(f"  Parsed {len(rows)} rows; columns: {reader.fieldnames}")

    # Revenue (inc GST). Column candidates across Insights schemas:
    #   Sales Summary: Revenue_inc_gst / Sales
    #   Sales by Product: '$ Sales' / 'Sale Amount' / 'Total Sales'
    revenue_inc = sum(
        parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales", "Sale Amount", "Total Sales"))
        for r in rows
    )
    # Total GST — if present, revenue_net = revenue_inc - total_tax; else fall back to /1.1
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
    # COGS
    cogs = sum(parse_num(col(r, "COGS", "Cost", "Cost of Goods Sold")) for r in rows)
    gp = revenue_net - cogs

    # Category breakdown — only meaningful if 'Category' is a column
    category_breakdown = {}
    if any((r.get("Category") or "").strip() for r in rows):
        for r in rows:
            cat = (r.get("Category") or "Uncategorised").strip()
            category_breakdown.setdefault(cat, {"rev": 0.0, "cogs": 0.0, "qty": 0.0})
            category_breakdown[cat]["rev"] += parse_num(col(r, "Revenue_inc_gst", "$ Sales", "Sales"))
            category_breakdown[cat]["cogs"] += parse_num(col(r, "COGS", "Cost"))
            category_breakdown[cat]["qty"] += parse_num(col(r, "Qty", "Product Quantity", "Quantity"))

    # Product breakdown — for Sales-by-Product reports we can surface top movers
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

    # Uber Eats revenue — only in Sales Summary schemas that break out payment types
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
        "product_breakdown": product_breakdown[:20],  # top 20 by revenue
    }

deputy_file = DATA_DIR / f"deputy_{target.isoformat()}.json"
if not deputy_file.exists():
    deputy_data = None
else:
    with deputy_file.open() as f:
        d = json.load(f)
    kitchen_cost = sum(t["cost"] for t in d if t.get("dept") == "Kitchen")
    driver_cost = sum(t["cost"] for t in d if t.get("dept") == "Driver")
    total_wages = kitchen_cost + driver_cost
    deputy_data = {"kitchen_wages": kitchen_cost,"driver_wages": driver_cost,"total_wages": total_wages,"kitchen_hours": sum(t.get("hours", 0) for t in d if t.get("dept") == "Kitchen"),"driver_hours": sum(t.get("hours", 0) for t in d if t.get("dept") == "Driver")}

if lightspeed_data and lightspeed_data.get("uber_eats_rev"):
    uber_commission = lightspeed_data["uber_eats_rev"] / 1.1 * 0.30
else:
    uber_commission = 0

# Initialise all downstream variables so both branches (Insights present/absent) are safe.
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
    delivery_dollars = driver_dollars + uber_commission
    delivery_pct = delivery_dollars / rev_ex * 100 if rev_ex else 0
else:
    rev_ex = cogs_dollars = cogs_pct = None
    wages_dollars = wages_pct = None
    delivery_dollars = delivery_pct = None

with BASELINE.open() as f:
    baseline = json.load(f)
targets = baseline["targets_and_alerts"]

def status(v, c):
    if v is None: return "unknown"
    if v >= c["red"]: return "red"
    if v >= c["amber"]: return "amber"
    if v <= c["target"]: return "green"
    return "yellow"

cogs_status = status(cogs_pct, targets["cogs"])
wages_status = status(wages_pct, targets["wages"])
delivery_status = status(delivery_pct, targets["delivery"])

record = {"date": target.isoformat(),"generated_at": datetime.utcnow().isoformat() + "Z","venue": "Marilynas","data_status": {"lightspeed": "ok" if lightspeed_data else "missing","deputy": "ok" if deputy_data else "missing"},"sales": {"revenue_inc_gst": round(lightspeed_data["revenue_inc"], 2) if lightspeed_data else None,"revenue_ex_gst": round(rev_ex, 2) if rev_ex else None,"cogs_dollars": round(cogs_dollars, 2) if cogs_dollars is not None else None,"cogs_pct": round(cogs_pct, 1) if cogs_pct is not None else None,"gp_dollars": round(lightspeed_data["gp"],2) if lightspeed_data else None,"gp_pct": round(lightspeed_data["gp_pct"], 1) if lightspeed_data else None,"uber_eats_revenue": round(lightspeed_data.get("uber_eats_rev", 0), 2) if lightspeed_data else 0},"wages": {"kitchen_dollars": round(deputy_data["kitchen_wages"], 2) if deputy_data else None,"driver_dollars": round(deputy_data["driver_wages"], 2) if deputy_data else None,"total_dollars": round(wages_dollars, 2) if wages_dollars is not None else None,"wages_pct": round(wages_pct, 1) if wages_pct is not None else None},"delivery": {"uber_eats_commission_dollars": round(uber_commission, 2),"own_driver_dollars": round(driver_dollars, 2),"total_dollars": round(delivery_dollars,2) if delivery_dollars is not None else None,"delivery_pct": round(delivery_pct,1) if delivery_pct is not None else None},"alerts": {"cogs": cogs_status,"wages": wages_status,"delivery": delivery_status},"targets": targets, "top_products": lightspeed_data.get("product_breakdown", []) if lightspeed_data else []}

out_file = DATA_DIR / f"mari_daily_{target.isoformat()}.json"
with out_file.open("w") as f:
    json.dump(record, f, indent=2)
print(f"Saved {out_file}")

history_file = DATA_DIR / "mari_daily_history.csv"
history_rows = []
if history_file.exists():
    with history_file.open() as f:
        history_rows = list(csv.DictReader(f))
history_rows = [r for r in history_rows if r["date"] != target.isoformat()]
nr = {"date": target.isoformat(),"revenue_ex_gst": record["sales"]["revenue_ex_gst"],"cogs_dollars": record["sales"]["cogs_dollars"],"cogs_pct": record["sales"]["cogs_pct"],"wages_dollars": record["wages"]["total_dollars"],"wages_pct": record["wages"]["wages_pct"],"delivery_dollars": record["delivery"]["total_dollars"],"delivery_pct": record["delivery"]["delivery_pct"],"gp_dollars": record["sales"]["gp_dollars"],"gp_pct": record["sales"]["gp_pct"],"cogs_alert": cogs_status,"wages_alert": wages_status,"delivery_alert": delivery_status}
history_rows.append(nr)
cutoff = target - timedelta(days=60)
history_rows = [r for r in history_rows if date.fromisoformat(r["date"]) > cutoff]
history_rows.sort(key=lambda r: r["date"])
with history_file.open("w", newline="") as f:
    if history_rows:
        w = csv.DictWriter(f, fieldnames=list(nr.keys()))
        w.writeheader()
        w.writerows(history_rows)
print(f"History: {len(history_rows)} rows")
