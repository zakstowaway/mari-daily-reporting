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

## Deploying the dashboard

Edit `dashboard/index.html`, then `git commit` + `git push`. GitHub Pages
redeploys automatically (custom domain: app.stowawaybar.com, served from the
`dashboard/` folder as site root — so `dashboard/index.html` is `/`).

There is **no need for the old patch_index_v*.py / push_*.py scripts**. They
existed only because earlier sessions had no persistent clone to push from, so
they mutated a scratch copy and PUT it via the GitHub contents API. They are
archived (untracked) in `_archive/patch-scripts-2026-07/` for reference only.

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

    python3 scripts/pull_xero_pay_weekly.py     # -> data/xero_pay_weekly.json
    # then dispatch the Employee Map + Rebuild Wages workflows

**Do not** use `backfill_wages_deputy.py` or `backfill_dept_split.py` — both are
deprecated and exit immediately. They cost salaried staff at hours x rate.

New salary-earners are caught by `check_salaried_roster.py` (launchd:
com.stowaway.salariedcheck, Mondays 10:40). Owners live in `_corp_payroll_only`
and reach the P&L via the residual precisely because they're absent from Deputy.
