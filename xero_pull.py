#!/usr/bin/env python3
"""Weekly Xero pull for the daily-reporting dashboard. Runs on the Mac.

Pulls from the Xero Accounting API (P&L report filtered by the two tracking
categories) and rebuilds two repo feeds:

  data/xero_cogs_weekly.csv       — purchases (COGS accounts) ex-GST by
                                    venue/dept per week. Same schema as the
                                    scrape-derived feed; API replaces the
                                    manual "Weekly Purchases by Tracking"
                                    report scrape.
  data/xero_overheads_monthly.csv — group operating overheads per month
                                    (ex-Misc, ex-wages, ex-dep/interest),
                                    allocated to venues: rent direct
                                    (Stow $15,000/mo, HG $5,245/mo), the
                                    rest by plan-basis FIXED_OH share
                                    (Stow 65.6% / HG 21.3% / Mari 13.1%).

Also dumps each raw P&L JSON to <repo-mirror>/xero_raw/ locally for
inspection/tuning of the account lists.

Token handling: refresh token rotates on every use (Xero 60-day rotating
refresh tokens) — the new one is persisted back to the cache atomically
BEFORE any parsing, so a crash can't strand us with a burned token.

CLI:
  python3 xero_pull.py                 # last 8 complete Mon-Sun weeks (upsert)
  python3 xero_pull.py --weeks 33      # deeper backfill
  python3 xero_pull.py --no-push       # build CSVs, skip GitHub push
"""
import base64, csv, io, json, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import date, timedelta
from pathlib import Path

SECRETS_DIR = Path("/Users/stowaway/Documents/STOW/Sales Reports/Daily Reporting/.secrets")
APP_FILE = SECRETS_DIR / "xero_app.json"
CACHE_FILE = SECRETS_DIR / "xero_token_cache.json"
PAT_FILE = SECRETS_DIR / "github_pat_v2.txt"
WORK_DIR = Path(__file__).parent / "xero_work"
WORK_DIR.mkdir(exist_ok=True)
REPO = "zakstowaway/mari-daily-reporting"

# ---- canon (weekly-report skill + Olly's BEP plan) ----
COGS_ACCOUNTS = {
    "Purchases - Food": "food",
    "Purchases - Beverages": "bev",
    "Purchases Other COGS": "other",
    "Purchases - Packaging": "other",
}
# Excluded from overheads entirely:
OH_EXCLUDE_SUBSTR = ["wages", "salaries", "superannuation", "depreciation", "amortisation",
                     # Payroll tax + workers comp are payroll ON-COSTS: they already
                     # come through group_payroll -> the wage on-cost residual on the
                     # dashboard, so they must NOT also sit in overheads or they get
                     # counted twice. (Zak, 2026-07-20: was overstating group OH by
                     # ~$3.2k Apr, ~$27.7k May incl a 'Workers Compensation - Prior
                     # Year' true-up, ~$5.0k Jun.) "workers compensation" also catches
                     # the prior-year variant.
                     "payroll tax", "workers compensation",
                     "interest", "purchases", "cost of sales", "rent - ",
                     # P&L summary rows (the walk flattens sections):
                     "gross profit", "net profit", "operating profit", "total ",
                     # delivery lane lives on the dashboard separately (canon:
                     # overheads exclude delivery + uber commission):
                     "service & delivery fees", "uber direct", "surcharge fees", "doordash"]
# Rent: Olly's BEP plan basis, NOT Xero actuals. Total is $20,245/mo — the single
# lease for the whole Freshwater premises, per Olly's BEP ("Entire Venue", 21 Jul).
# It's ONE lease, so Mari (which trades out of Stow's kitchen) doesn't pay separate
# rent — its share is carved from the shared total, not added on top. Zak (2026-07-22)
# set Mari at $3,000/mo; that comes out of Stow's portion so the group total stays
# $20,245 and still ties to Olly's BEP. (2026-07-16 audit: HG matches Xero to the
# dollar; Stow's Xero rent runs ~$12,554/mo, so $12k here is close to actual.)
RENT_MONTHLY = {"stow": 12000.0, "hg": 5245.0, "mari": 3000.0}   # = $20,245/mo (Olly BEP)
PLAN_OH_SHARE = {"stow": 0.656, "hg": 0.213, "mari": 0.131}   # FIXED_OH_WEEKLY ratios (BEP)
# Payroll on-costs. Defined once, at module scope: the finance lane has to skip
# these BEFORE claiming anything matching "interest", or 'Super Guarantee
# Interest & Admin Fee' lands in both payroll and finance and gets counted twice.
PAYROLL_SUBSTR = ("wages", "salaries", "superannuation", "super guarantee",
                  "payroll tax", "workers compensation")
FINANCE_SUBSTR = ("interest", "depreciation", "amortisation")

# ex-Misc canon: these tracking options never count, anywhere, in any feed.
# 'To Be Reviewed' is Xero's own holding pen for coding nobody has confirmed —
# it is miscellaneous by definition and belongs here (2026-07-16 audit: it held
# $14,242.91 of Rent - Stowaway for Apr-Jun, which slipped through because the
# set only matched names starting 'Miscellaneous').
MISC_OPTIONS = {"Miscellaneous", "Miscellaneous K", "Miscellaneous Z", "To Be Reviewed"}
# Any tracking option we've never seen before is REPORTED rather than silently
# swept into a venue — a new option is a business decision, not a parse detail.
KNOWN_OPTIONS = MISC_OPTIONS | {"Admin", "Bar", "Kitchen", "Marilyna's Pizza",
                                "Harry Gatos", "Fitout Costs", "Wages & Staffing Costs",
                                "Unassigned", "Total", ""}
STOW_TRACKING_CAT = "Stowaway"     # tracking category names in Xero
HG_TRACKING_CAT = "Harry Gatos"


def token():
    app = json.loads(APP_FILE.read_text())
    cache = json.loads(CACHE_FILE.read_text())
    basic = base64.b64encode(f"{app['client_id']}:{app['client_secret']}".encode()).decode()
    req = urllib.request.Request("https://identity.xero.com/connect/token",
        data=urllib.parse.urlencode({"grant_type": "refresh_token",
            "refresh_token": cache["refresh_token"]}).encode(),
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    tok = json.loads(urllib.request.urlopen(req).read())
    # persist rotated refresh token IMMEDIATELY (atomic)
    cache["refresh_token"] = tok["refresh_token"]
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2))
    tmp.replace(CACHE_FILE)
    return tok["access_token"], cache["tenant_id"]


def api_get(access, tenant, path, params=None):
    url = f"https://api.xero.com/api.xro/2.0/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {access}", "Xero-tenant-id": tenant, "Accept": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "5")) + 1
                print(f"  429 rate-limited, sleeping {wait}s"); time.sleep(wait); continue
            print(f"HTTP {e.code} {url}: {e.read()[:400]}", file=sys.stderr); raise
    raise RuntimeError("rate-limit retries exhausted")


def pl_report(access, tenant, dfrom, dto, tracking_cat_id=None):
    params = {"fromDate": dfrom, "toDate": dto, "standardLayout": "true"}
    if tracking_cat_id:
        params["trackingCategoryID"] = tracking_cat_id
    return api_get(access, tenant, "Reports/ProfitAndLoss", params)


def parse_pl(report_json):
    """-> (column_titles, {account_title: [values]}, {account_title: section})"""
    rpt = report_json["Reports"][0]
    header = next(r for r in rpt["Rows"] if r["RowType"] == "Header")
    cols = [c.get("Value", "") for c in header["Cells"]][1:]
    out, sections = {}, {}
    def walk(rows, section=""):
        for r in rows:
            if r["RowType"] == "Section":
                walk(r.get("Rows", []), r.get("Title") or section)
            elif r["RowType"] in ("Row", "SummaryRow"):
                cells = r.get("Cells", [])
                if not cells:
                    continue
                title = cells[0].get("Value", "")
                vals = []
                for c in cells[1:]:
                    v = c.get("Value", "")
                    try: vals.append(float(str(v).replace(",", "")))
                    except ValueError: vals.append(0.0)
                out[title] = vals
                sections[title] = section
    walk(rpt["Rows"])
    return cols, out, sections


def main():
    weeks_back = 8
    push = True
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--weeks": weeks_back = int(args[i + 1])
        if a == "--no-push": push = False

    access, tenant = token()
    print(f"Xero auth OK (tenant {tenant})")

    # tracking category ids
    cats = api_get(access, tenant, "TrackingCategories")["TrackingCategories"]
    cat_ids = {c["Name"]: c["TrackingCategoryID"] for c in cats}
    stow_cat = cat_ids.get(STOW_TRACKING_CAT)
    hg_cat = cat_ids.get(HG_TRACKING_CAT)
    print("Tracking categories:", {k: v[:8] for k, v in cat_ids.items()})

    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7 or 7
    last_sunday = today - timedelta(days=days_since_sunday)

    # ---- weekly purchases by tracking ----
    weekly_rows = {}   # (week_ending, venue) -> {food, bev, other}
    unknown_opts = {}  # tracking options nobody has classified
    for w in range(weeks_back):
        we = last_sunday - timedelta(days=7 * w)
        ws = we - timedelta(days=6)
        for cat_name, cat_id in (("stowaway_cat", stow_cat), ("hg_cat", hg_cat)):
            if not cat_id: continue
            rpt = pl_report(access, tenant, ws.isoformat(), we.isoformat(), cat_id)
            (WORK_DIR / f"pl_{cat_name}_{we}.json").write_text(json.dumps(rpt))
            cols, rows, _sections = parse_pl(rpt)
            for acct, dept in COGS_ACCOUNTS.items():
                vals = rows.get(acct)
                if not vals: continue
                for i, opt in enumerate(cols):
                    if i >= len(vals): break
                    amt = vals[i]
                    if not amt or opt.lower() in ("total", ""): continue
                    if opt not in KNOWN_OPTIONS:
                        # A tracking option nobody has classified. Don't guess a
                        # venue for it — say so, loudly, and leave the money out.
                        unknown_opts[(cat_name, opt)] = unknown_opts.get((cat_name, opt), 0.0) + amt
                        continue
                    if cat_name == "stowaway_cat":
                        if opt in MISC_OPTIONS or opt == "Admin": continue
                        ven = "mari" if opt == "Marilyna's Pizza" else "stow" if opt in ("Bar", "Kitchen") else None
                    else:
                        ven = "hg" if opt in ("Bar", "Kitchen") else None
                    if ven is None: continue
                    d = "bev" if opt == "Bar" else "food" if opt == "Kitchen" else "food"
                    if ven == "mari": d = "food"
                    if acct not in ("Purchases - Food", "Purchases - Beverages"): d = "other"
                    cell = weekly_rows.setdefault((we.isoformat(), ven), {"food": 0.0, "bev": 0.0, "other": 0.0})
                    cell[d] += amt
        print(f"week ending {we}: {[k for k in weekly_rows if k[0] == we.isoformat()]}")

    if unknown_opts:
        print("\n*** UNKNOWN TRACKING OPTIONS — money left OUT of the venue COGS feed ***")
        for (cat, opt), amt in sorted(unknown_opts.items(), key=lambda kv: -abs(kv[1])):
            print(f"    {cat:<14} '{opt}'  ${amt:,.2f}")
        print("    -> classify it in KNOWN_OPTIONS/MISC_OPTIONS, or fix the coding in Xero")

    # merge into existing feed (upsert by week+venue)
    feed = WORK_DIR / "xero_cogs_weekly.csv"
    existing = {}
    pat = PAT_FILE.read_text().strip()
    def gh(method, path, body=None):
        data = json.dumps(body).encode() if body else None
        for attempt in range(4):
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", method=method,
                headers={"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json", "User-Agent": "x"})
            if data: req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, data) as r: return r.status, json.loads(r.read() or "{}")
            except urllib.error.HTTPError as e:
                # GitHub 5xx is transient (the 2026-07-20 pull failed all three
                # pushes on a 503 blip). Retry with backoff rather than fail silent.
                if e.code in (500, 502, 503, 504) and attempt < 3:
                    print(f"  GitHub {e.code} on {path} — retry {attempt+1}/3"); time.sleep((attempt + 1) * 4); continue
                return e.code, json.loads(e.read() or "{}")
        return 0, {}

    st, info = gh("GET", "contents/data/xero_cogs_weekly.csv")
    sha = None
    if st == 200:
        sha = info["sha"]
        for r in csv.DictReader(io.StringIO(base64.b64decode(info["content"]).decode())):
            existing[(r["week_ending"], r["venue"])] = r
    for (we, ven), c in weekly_rows.items():
        tot = c["food"] + c["bev"] + c["other"]
        existing[(we, ven)] = {"week_ending": we, "venue": ven,
            "actual_cogs_ex_gst": round(tot, 2), "food_ex_gst": round(c["food"], 2),
            "bev_ex_gst": round(c["bev"], 2), "other_ex_gst": round(c["other"], 2)}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["week_ending", "venue", "actual_cogs_ex_gst", "food_ex_gst", "bev_ex_gst", "other_ex_gst"], lineterminator="\n")
    w.writeheader()
    for k in sorted(existing): w.writerow(existing[k])
    feed.write_text(buf.getvalue())
    print(f"xero_cogs_weekly.csv: {len(existing)} rows")

    # ---- monthly group overheads (ex-Misc, ex-wages, ex-dep/int) ----
    oh_rows = []
    for m in range(4):
        mstart = date(today.year, today.month, 1)
        for _ in range(m):
            mstart = (mstart - timedelta(days=1)).replace(day=1)
        nxt = (mstart.replace(day=28) + timedelta(days=4)).replace(day=1)
        mend = min(nxt - timedelta(days=1), today)
        rpt = pl_report(access, tenant, mstart.isoformat(), mend.isoformat())
        (WORK_DIR / f"pl_group_{mstart:%Y-%m}.json").write_text(json.dumps(rpt))
        cols, rows, sections = parse_pl(rpt)
        # exclude Misc: pull the Stowaway-tracked P&L and subtract Misc columns
        # (EX-MISC IS CANON across every Xero feed in this app)
        rpt_m = pl_report(access, tenant, mstart.isoformat(), mend.isoformat(), stow_cat)
        cols_m, rows_m, _ = parse_pl(rpt_m)
        misc_idx = [i for i, c in enumerate(cols_m) if c in MISC_OPTIONS]
        def net_of_misc(acct):
            vals = rows.get(acct) or []
            v = vals[-1] if vals else 0.0
            misc = sum(rows_m.get(acct, [0.0] * len(cols_m))[i] for i in misc_idx if i < len(rows_m.get(acct, []))) if misc_idx else 0.0
            return v - misc
        overheads = 0.0
        detail = {}
        stock_movement = 0.0
        leave_provision = 0.0
        for acct, vals in rows.items():
            a = acct.lower()
            if acct.startswith("Total") or not vals: continue
            sec = (sections.get(acct) or "").lower()
            # Month-end accrual journals get their own lanes (never overheads):
            #   Closing Stock Movement (Cost of Sales) -> actual-COGS adjustment
            #   Annual Leave Provision Movement (OpEx) -> wage accrual
            if "stock movement" in a:
                stock_movement += net_of_misc(acct); continue
            if "provision movement" in a:
                leave_provision += net_of_misc(acct); continue
            if "operating expenses" not in sec: continue   # OpEx section ONLY
            if any(x in a for x in OH_EXCLUDE_SUBSTR): continue
            net = net_of_misc(acct)
            if net:
                overheads += net; detail[acct] = round(net, 2)
        # Total Cost of Sales (ex-Misc) — authoritative actual COGS incl the
        # stock-movement journal, for the closed-period actual-profit view.
        group_cos = net_of_misc("Total Cost of Sales")
        # Below-the-line: interest + depreciation/amortisation. Deliberately NOT
        # in overheads — overheads drive breakeven, and breakeven is a trading
        # decision: you can't roster your way out of a loan. But they're real
        # money leaving (interest) and real capital consumed (depreciation), and
        # excluding them entirely made the dashboard's "Profit" an operating
        # figure that quietly disagreed with the statutory one (Zak, 2026-07-16).
        # Emitted as their own lane so the admin view can bridge the two without
        # polluting any operating metric.
        group_finance = 0.0
        for acct in rows:
            a = acct.lower()
            if acct.startswith("Total") or "operating expenses" not in (sections.get(acct) or "").lower():
                continue
            # 'Super Guarantee Interest & Admin Fee' is payroll, not finance —
            # it's an ATO charge for late super. It matches "interest", so it
            # must be claimed by payroll first or it lands in both lanes.
            if any(x in a for x in PAYROLL_SUBSTR):
                continue
            if any(x in a for x in FINANCE_SUBSTR):
                group_finance += net_of_misc(acct)
        # Total payroll (ex-Misc): wages + super + payroll tax + workers comp.
        # Includes owners' salaries and statutory on-costs that Deputy never
        # sees — the dashboard's corporate-payroll share comes from the
        # residual (this minus Deputy-derived group wages), split by the plan
        # wage shares (board P&L canon: 62.4 / 20.7 / 17.0).
        # "super guarantee" catches 'Super Guarantee Interest & Admin Fee', which
        # fell through every rule and vanished (2026-07-16 audit): it doesn't
        # contain "superannuation" so it missed payroll, and it DOES contain
        # "interest" so OH_EXCLUDE_SUBSTR dropped it from overheads. ~$12.6k a
        # quarter of real payroll on-cost, counted nowhere. It's the ATO's charge
        # for super paid late, so it belongs with payroll — and is worth watching.
        group_payroll = 0.0
        for acct in rows:
            a = acct.lower()
            if acct.startswith("Total"): continue
            if "provision movement" in a: continue
            if any(x in a for x in PAYROLL_SUBSTR):
                group_payroll += net_of_misc(acct)
        # ---- delivery fees (canon: Uber/DoorDash/UberDirect -> Mari;
        # ME&U + Doshii surcharges -> Stow/HG split by revenue share, done
        # client-side on the dashboard) ----
        MARI_DELIVERY = ("Service & Delivery Fees - UberEats",
                         "Service & Delivery Fees - DoorDash",
                         "Uber Direct Delivery Fees")
        MEU_DELIVERY = ("Service & Delivery Fees - ME&U",
                        "Surcharge Fees Paid (Doshii/ME&U)")
        def acct_net(names):
            tot = 0.0
            for acct in names:
                vals = rows.get(acct)
                if not vals: continue
                v = vals[-1]
                misc = sum(rows_m.get(acct, [0.0]*len(cols_m))[i] for i in misc_idx if i < len(rows_m.get(acct, []))) if misc_idx else 0.0
                tot += v - misc
            return tot
        mari_fees = acct_net(MARI_DELIVERY)
        meu_fees = acct_net(MEU_DELIVERY)
        # UberEats account alone — lets the dashboard swap in REAL portal
        # service fees (ex-marketing) while keeping DoorDash + Uber Direct
        # from the books: portal_svc + (mari_uber_fees - mari_uber_only).
        mari_uber_only = acct_net(("Service & Delivery Fees - UberEats",))

        rent_total = sum(RENT_MONTHLY.values())
        frac = (mend.day) / ((nxt - timedelta(days=1)).day)   # partial-month scaling for rent
        row = {"month": f"{mstart:%Y-%m}", "group_overheads_ex_rent": round(overheads, 2),
               "mari_uber_fees": round(mari_fees, 2), "mari_uber_only": round(mari_uber_only, 2), "meu_fees": round(meu_fees, 2),
               "stock_movement": round(stock_movement, 2), "leave_provision": round(leave_provision, 2),
               "group_cos_ex_misc": round(group_cos, 2), "group_payroll": round(group_payroll, 2),
               "group_finance": round(group_finance, 2)}
        for ven in ("stow", "hg", "mari"):
            row[f"{ven}_overheads"] = round(RENT_MONTHLY[ven] * frac + overheads * PLAN_OH_SHARE[ven], 2)
        oh_rows.append(row)
        (WORK_DIR / f"oh_detail_{mstart:%Y-%m}.json").write_text(json.dumps(detail, indent=1))
        print(f"{mstart:%Y-%m}: group OH (ex-rent, ex-misc) ${overheads:,.0f}")
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["month", "group_overheads_ex_rent", "stow_overheads", "hg_overheads", "mari_overheads", "mari_uber_fees", "mari_uber_only", "meu_fees", "stock_movement", "leave_provision", "group_cos_ex_misc", "group_payroll", "group_finance"], lineterminator="\n")
    w.writeheader()
    for r in sorted(oh_rows, key=lambda r: r["month"]): w.writerow(r)
    (WORK_DIR / "xero_overheads_monthly.csv").write_text(buf.getvalue())

    if push:
        for repo_path, local in (("data/xero_cogs_weekly.csv", feed),
                                 ("data/xero_overheads_monthly.csv", WORK_DIR / "xero_overheads_monthly.csv")):
            content = local.read_bytes()
            st, info = gh("GET", f"contents/{repo_path}")
            body = {"message": f"Xero API pull {today.isoformat()}: {repo_path.split('/')[-1]}",
                    "content": base64.b64encode(content).decode(), "branch": "main"}
            if st == 200: body["sha"] = info["sha"]
            st, resp = gh("PUT", f"contents/{repo_path}", body)
            print(("OK" if st in (200, 201) else f"FAIL {st}"), repo_path)
        # nudge a deploy (Pages redeploys on Daily Pull completion)
        st, _ = gh("POST", "actions/workflows/daily_pull.yml/dispatches",
                   {"ref": "main", "inputs": {"venue": "marilynas"}})
        print("deploy nudge:", st)


if __name__ == "__main__":
    main()
