# Where this project lives

**Canonical working copy:** `/Users/Shared/ClaudeShared/STOW/Sales Reports/Daily Reporting`
(also reachable as `~/Documents/STOW/Sales Reports/Daily Reporting` — symlink, same folder.
ACLs grant both `zak` and `stowaway` full access.)

As of 2026-07-15 this folder is a **real git clone** of `zakstowaway/mari-daily-reporting`.
It had previously drifted ~3 days stale because it was a plain folder with no
git, so nobody could see it had fallen behind. Don't let that happen again:

    git status      # drift is now visible
    git pull        # before you start
    git push        # when you're done

## ⚠️ CRITICAL DATA MODEL — read before touching sales data (2026-07-24)

**All three venues share ONE Lightspeed till — the Stowaway POS.**
- **Marilyna's has NO till of its own.** Her sales ring through the Stow POS.
- Harry Gatos food also rings through the Stow POS.
- The Stow "Sales by Product" export `data/insights_stow_<date>.csv` is the WHOLE
  site — it carries Stow **+ HG + Mari** rows.
- `daily_aggregator.py` splits it by `classify_product()`: Stow keeps its own,
  HG gets `'hg'` rows, **Marilyna's = the `'m'` rows carved off the Stow till**
  (`classify_product(...) == 'm'`). Mari's own export is a CROSS-CHECK only,
  NEVER a source. There is NO separate Marilyna's Kounta login to hunt for.

**Sales enter ONLY via the emailed Insights CSV** (Lightspeed scheduled report →
Pipedream → `repository_dispatch` with `csv_base64`). There is NO sales API pull.

**To fix a day whose Stow export email never fired:**
1. From the logged-in Lightspeed tab, fetch the Stow export:
   `https://my.kounta.com/report/salesummarybyproduct?DateFrom=<d>&DateTo=<d>&CategoryID=0&SiteID=0&TerminalID=0&TabId=week&TypeId=product&tags=&noTax=0&export=true&txtDateFrom=<d>&txtDateTo=<d>`
   (`credentials:'include'`). The aggregator handles this format: no tax column →
   revenue_ex = revenue_inc / 1.1.
2. Ingest through the production path — `repository_dispatch` `stow-csv-arrived`,
   payload `{venue:"stowaway", csv_base64:<b64>}`. This writes the Stow export,
   pulls Deputy, aggregates **stow + hg**.
3. **THEN run the Mari aggregation** so her rows get carved from the same export:
   `workflow_dispatch daily_pull.yml {venue:"marilynas", target_date:"<d>"}`.
   (The stow-csv-arrived run does NOT re-aggregate Mari automatically.)

**The Stow export is a SINGLE POINT OF FAILURE for the whole group.** Sales flow:
Lightspeed scheduled "Sales by Product" email → a Pipedream workflow (NOT in this
repo; separate from `pipedream/uber_direct_ingest.js`) → `repository_dispatch`
(`stow-csv-arrived` / `hg-csv-arrived` / `insights-csv-arrived`=mari). Pipedream
works when an email arrives (HG's fired 2026-07-23); when Stow's doesn't fire,
Mari + part of HG starve too. 2026-07-24: only HG's email came for 23 Jul, so the
Group showed HG-only ($1,685) until I hand-pulled Stow and ingested it. If a day
looks wrong/partial, FIRST check whether the Stow export landed
(`ls data/insights_stow_<date>.csv`); the dashboard now also flags venues
"awaiting import" on the group day view. Root cause when Stow's is missing is
upstream (Lightspeed schedule disabled, or email not reaching Pipedream) — not
the aggregator.

**Closed-week wages/leave = Xero** (Mac-only pull; leave from payslip
LeaveEarningsLines). Owners (Oliver, Bryony) = corp payroll, never on Deputy/venue lines.

### Sales email ingestion — FREE, no Pipedream (2026-07-24)

Pipedream's free tier ran out mid-morning 2026-07-24, so HG's 05:00 email got
through but Stow's 05:30 + Mari's 06:00 didn't — starving the whole group (Mari +
part of HG derive from the Stow export). We do NOT pay $45/mo for an email
forwarder. Replacement: `.github/workflows/ingest_insights_email.yml` +
`scripts/ingest_insights_email.py` — a GitHub Action that reads the Insights
"Daily Sales Auto" emails and fires the SAME `{stow,hg,insights}-csv-arrived`
dispatches the daily pull already consumes. Polls every 20 min in the morning
window, so a late email is caught next run and re-runs are no-ops (only UNSEEN
mail is processed, then marked \Seen).

**Why Gmail, not M365.** This tenant blocks every self-serve Microsoft Graph
path: Zak's account can't register apps (401); user consent is disabled (the
Graph CLI client hits an admin-approval wall, AADSTS65001-style); and the Office
public client isn't preauthorised for Graph (AADSTS65002). So we route the three
Lightspeed schedules to a **dedicated free Gmail** and read THAT over IMAP with a
Google **app password** — no admin anywhere.

**One-time setup:**
1. Create a dedicated Gmail (nobody reads it), e.g. `stowawaysales@gmail.com`.
   Turn on 2-Step Verification, then generate an **App password**
   (myaccount.google.com → Security → App passwords) — 16 chars.
2. Point the three Lightspeed schedules at that Gmail: Insights → Reports →
   "Product sales" → Schedules → each Daily auto → recipient = the Gmail.
3. Repo secrets: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (the 16-char app password),
   `GH_DISPATCH_PAT` (PAT with repo scope — fires repository_dispatch).

Dedupe is the IMAP `\Seen` flag (dedicated inbox, so "unseen" is reliable) — no
token rotation, no ledger. $0, always-on, no admin. Note: M365 IMAP is NOT an
option here (basic-auth/app-passwords disabled by the tenant) — Gmail is.


## Deploying the dashboard (modularised 2026-07-23 — see dashboard/_shared/README.md)

The site is built by `scripts/build_site.py` and served at app.stowawaybar.com:
`/` = `dashboard/home/` (module picker), `/sales/` = the P&L dashboard,
`/recipes/`, `/bookings/`, `/admin/`.

**The sales dashboard is NO LONGER one big index.html.** `dashboard/sales/index.html`
is a ~70KB SHELL (markup + config + bootstrap); ALL logic lives in modules:
`dashboard/_shared/pnl.js` (pure P&L maths), `util.js` (helpers/formatting),
`data.js` (feed loaders), `render.js` (DOM + handlers). **Do not put business
logic in index.html** — `scripts/arch_guard.py` fails CI *and* the deploy if you
do (it also runs 3 JS test suites + the P&L conservation check). `scripts/schema_guard.py`
guards the history CSVs. `reconcile_wages.py` proves every Xero dollar classifies.

**Deploy trap — work in an ISOLATED clone, never this mounted folder.** The cron
(Daily Pull / Rebuild Wages) does `git pull --rebase` on THIS working tree and
will silently clobber in-progress edits mid-session. Pattern: `git clone` to /tmp,
edit + commit + push there, so automation can't stomp you. A push to `dashboard/**`
(or a Daily Pull / Wages Backfill / Rebuild Wages / Roster Pull run) triggers the
`deploy_dashboard` GitHub Action which rebuilds Pages. Data-only commits need a
`dashboard/**` touch OR one of those workflows to redeploy.

The old patch_index_v*.py / push_*.py scripts are archived in
`_archive/patch-scripts-2026-07/` — do not use them.

## Auth

`git push` authenticates via a credential helper configured in `.git/config`
that reads the PAT from `.secrets/github_pat_v2.txt` (gitignored). The token is
not stored in git config itself. If pushes start failing, check that file first.

Note: git needs a safe.directory exception because the folder is owned by `zak`:

    git config --global --add safe.directory "/Users/Shared/ClaudeShared/STOW/Sales Reports/Daily Reporting"

## Known stale copies (do not use)

- `.../local_5ea388ea-.../outputs/push/`  — session scratch, was the only copy
  of v16.4/v16.5 work until 2026-07-15. Now redundant.
- `.../local_5ea388ea-.../outputs/repo/`  — Jul 12 snapshot, no git.
- `../\_daily-reporting-backup-2026-07-15.tgz` — pre-adoption safety snapshot.

## The Lightspeed email reports: what each one must contain

**There is one till.** Stowaway's POS rings up all three brands. Marilyna's has no
till of its own; Harry Gatos food is rung on the Stow till too. Every venue's
"own" CSV is a *filter over the same POS data*. That has one consequence people
keep re-discovering the hard way:

> **Stow's export must stay the FULL SITE report.** It is not "dirty" — two other
> venues read their revenue out of it.

    Stow's export ──┬── 'm'   rows ──► Marilyna's   (coverage guard cross-checks
                    │                                her report against these)
                    └── 'hgf' rows ──► Harry Gatos  (~$585/day, ~$213k/yr,
                                                     concentrated on MONDAYS:
                                                     07-06 $3,233, 07-13 $2,544)

`daily_aggregator.py` **strips both off Stow's own totals** (line ~310). So
narrowing Stow's report to "only Stow RGs" *does not change a single Stow
number* — it just deletes Harry Gatos' Monday revenue and blinds the Mari guard.
It looks like a tidy-up from inside Lightspeed and costs six figures a year in
silence. This was nearly shipped on 2026-07-16. A tripwire now shouts
`STOW EXPORT LOOKS NARROWED` if the export ever arrives with zero cross-venue
rows (Mari rings through Stow every trading day, so zero means the filter moved,
not that nobody ordered pizza).

**Mari's export** (`Mari Daily Sales Auto`) must include `Dine-in Pizza` and
`Add-ons - Pizza`. When it doesn't, Stow strips those rows and Mari never
receives them, so the revenue reaches **no venue at all** — $612.70 on 07-14,
$375.84 on 07-11. The aggregator now recovers them and prints `*** RECOVERED`;
that is a **net, not a repair** — the filter is the fix. The recovery is derived
from the gap, so it goes inert on its own once the filter is right.

**Mari's RG set is deliberately wider** than the weekly-report skill's
`Marilynas-strict` (which excludes Dine-in Pizza). Strict answers "what would we
lose if Mari closed?"; this answers "whose revenue is it?". Both correct. Don't
reconcile them.

## Running the aggregator by hand

    python3 scripts/daily_aggregator.py --venue stowaway 2026-07-14

**The `--venue` flag is required.** Venue is NOT positional — `daily_aggregator.py
stowaway 2026-07-14` silently aggregates *Marilyna's* (the default at line 223)
and looks like it worked. Some older notes have it wrong.

Re-running the aggregator **rewrites `wages_*` from the daily Deputy JSON using
the provisional model**, undoing the Xero-actuals rebuild for any day it touches.
Always follow it with a Rebuild Wages over **whole payroll weeks** (Mon–Sun).

## Wages: how they're costed (2026-07-15 rebuild)

Deputy knows who clocked on. Only Xero knows what they were paid. So:

  * **Closed weeks** — costed from `data/xero_pay_weekly.json` (what payroll
    actually paid), allocated pro-rata across the shifts each person logged.
    Hours decide WHERE the money lands; Xero decides how much.
  * **The open week** — estimated via `scripts/wage_model.py`: a salaried person
    costs annual/52 per week regardless of hours logged. This is an estimate
    standing in for Xero until the pay run posts.

`rebuild_wages.py` runs nightly over the current + previous payroll week. That's
load-bearing, not belt-and-braces: salaried cost is only knowable once a week is
known, and Deputy's Cost lands on APPROVAL (often days later), so re-reading the
fortnight is the only way approvals ever land.

Refresh the Xero side on the Mac (the token rotates, so Actions would burn it):

    python3 scripts/pull_xero_pay_weekly.py     # -> xero_pay_weekly.json + xero_super_weekly.json + xero_leave_weekly.json
    # then dispatch the Employee Map + Rebuild Wages workflows

**Closed-week LEAVE (added 2026-07-24).** `pull_xero_pay_weekly.py` now also writes
`data/xero_leave_weekly.json` from each payslip's LeaveEarningsLines (endpoint is
`/Payslip/{id}` SINGULAR, wrapped in `"Payslip"`; leave $ = NumberOfUnits x
RatePerUnit — there is no Amount field). `rebuild_wages.py` splits that leave OUT
of the venue wage line into `leave_dollars` on the register's leave days, so
"operational wages" excludes leave and the group leave toggle shows what payroll
paid. It is INERT until `xero_leave_weekly.json` exists (no change to any number).
The dashboard's leave figure is $0 until you run the Xero pull for those weeks.

**Do not** use `backfill_wages_deputy.py` or `backfill_dept_split.py` — both are
deprecated and exit immediately. They cost salaried staff at hours x rate.

New salary-earners are caught by `check_salaried_roster.py` (launchd:
com.stowaway.salariedcheck, Mondays 10:40). Owners live in `_corp_payroll_only`
and reach the P&L via the residual precisely because they're absent from Deputy.
