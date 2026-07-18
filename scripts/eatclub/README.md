# scripts/eatclub

EatClub monitoring across the three venues. Pure logic + config; the daily
runners (portal pull, hourly ingest, dashboard render) sit on top of this.

## Files

| file | what |
|---|---|
| `config.py` | per-venue EatClub config + the RG split that separates Stowaway from Marilyna's on the shared Stow till. Mirrors `daily_aggregator.classify_product` exactly. |
| `metrics.py` | `contribution_*` (net of discount/commission/COGS, ex-GST, Decimal) and the verdict functions: `assess_dinein` (HG, Stowaway) and `assess_takeaway` (Marilyna's). |
| `baseline.py` | pre-launch day-of-week baselines. Dine-in = window revenue; Marilyna's = `delivery_dollars` from `mari_daily_history.csv`. |
| `test_*.py` | seeded with the real HG nights pulled 2026-07-18. |

Run: `python3 -m pytest scripts/eatclub`

## The one fact that shapes everything

**Marilyna's has no till.** It is Reporting Groups on the Stowaway POS
(`daily_aggregator.py`). So:

- Stowaway and Marilyna's are separated by **Reporting Group**, not by site.
- `salesummarybyhour` returns *site* hour-totals and **cannot** separate them.
  Stowaway's clean dinner window therefore needs the **Custom Insights
  `Stow Hourly RG Auto`** feed (hour x RG), split by `config.is_stowaway_proper_row`.
- HG is its own site, so its existing `salesummarybyhour` path is unchanged.

## Two methods, because two channels

- **Dine-in (HG, Stowaway):** `assess_dinein`. Full-price window = offer-window
  revenue minus EatClub full bills, vs same-DOW pre-launch baseline. Respects the
  rescue-tier reverse-causality rule.
- **Takeaway (Marilyna's):** `assess_takeaway`. A pickup brand cannibalises
  *delivery*, not walk-ins. Compares total off-premise (EatClub + Uber/own-driver)
  against the pre-launch delivery baseline: flat = channel substitution, up =
  incremental.

## NOT built yet — pending a real feed sample

The **hourly RG CSV parser** is deliberately absent. The `Stow Hourly RG Auto`
Custom Insights report does not exist yet, so its exact columns / timestamp format
are unknown. Per ARCHITECTURE.md ("build your parser around what you observed, not
what you assume" / "refuse to guess"), the ingest is written only once one real CSV
has landed. Contract it must satisfy: one row per (date, hour, reporting_group)
with inc- and ex-GST revenue; the aggregator sums hours in `window_hours` over the
rows passing the venue's `row_filter`.

## Before contribution numbers are trusted

`cost_blend` is set only for HG (0.22, measured). Stowaway and Marilyna's are
`None` in `config.py` on purpose — set each from its own mix, don't borrow HG's.
`launch_date` is `None` for both new venues (Stowaway is still behind the EatClub
onboarding tour); the baseline has no cutoff until these are dated.
