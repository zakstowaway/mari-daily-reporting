# Marilyna's Daily Reporting Pipeline

Auto-updating dashboard showing yesterday's revenue, COGS %, wages %, delivery %, and GP % vs targets.

## Runs completely claude-less

- **Data collection:** GitHub Actions cron at 6am AEST daily
- **Dashboard hosting:** GitHub Pages (static)
- **Data sources:** Lightspeed Insights (via Zapier webhook) + Deputy API
- **Cost:** $0/month (all within free tiers)
