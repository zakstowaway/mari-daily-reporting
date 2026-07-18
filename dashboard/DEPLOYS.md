# Dashboard deploy log

Manual redeploys (the auto-deploy only fires on `dashboard/**` pushes and after
the daily-pull workflows, so a data-only change needs a nudge like this).

- 2026-07-19 — Rebuild to pick up the EatClub margin correction. Merged the
  aggregator change + backfilled give-away facts and recomputed every EatClub day
  (Stowaway 13–18 Jul, Harry Gatos 08–17 Jul); reported GP is now net of the
  EatClub discount + commission. This commit exists to trigger the Pages rebuild
  so the corrected `data/*_daily_*.json` are served.
