# Stowaway Hospitality Group — Product Sales API

Product-level sales history for Stowaway Bar, Harry Gatos, and Marilyna's,
served as static JSON from `app.stowawaybar.com` so any Claude session (web,
mobile, Cowork, plugin) can query fresh product data without a local clone.

**Refresh cadence:** rebuilt on every daily pull (weekday mornings, Sydney
time). Freshness is stamped in `generated_at` on every file.

## Endpoints

| URL | Purpose | Approx. size |
|-----|---------|-------------|
| `https://app.stowawaybar.com/sales/products/latest.json` | Freshness stamp + coverage counts. Start here. | ~1 KB |
| `https://app.stowawaybar.com/sales/products/index.json` | Every product with lifetime qty + revenue. Use for search/lookup. | ~230 KB |
| `https://app.stowawaybar.com/sales/products/rollup_stow.json` | Full weekly history for every Stowaway Bar product. | ~680 KB |
| `https://app.stowawaybar.com/sales/products/rollup_hg.json` | Full weekly history for every Harry Gatos product. | ~140 KB |
| `https://app.stowawaybar.com/sales/products/rollup_mari.json` | Full weekly history for every Marilyna's product. | ~540 KB |

Venue codes: `stow` = Stowaway Bar, `hg` = Harry Gatos, `mari` = Marilyna's.

## Conventions

- **All revenue is ex-GST.** GST is 10% in Australia — inc-GST = ex × 1.1.
- **Weeks are Monday–Sunday, indexed by their Sunday date** in the `we`
  (week_ending) field, ISO format `YYYY-MM-DD`.
- **Trading days:** Stowaway is closed Mondays; Harry Gatos is closed Tuesdays;
  Marilyna's has no till of its own (revenue is attributed from the Stowaway
  till).
- **Quantity:** unit counts. For 5-piece serves like Arancini Balls [5pc],
  qty=1 means one 5-piece serve, not five balls.
- **Timestamps in `generated_at`** are ISO 8601 in Australia/Sydney time
  (`+10:00`).

## Schemas

### `latest.json`

```json
{
  "generated_at": "2026-07-22T19:25:13+10:00",
  "coverage": {
    "stow": {"label": "Stowaway", "first_week_ending": "2025-06-01",
             "last_week_ending": "2026-07-19", "weeks": 60, "products": 501},
    "hg":   {"label": "Harry Gatos", "first_week_ending": "2025-06-01",
             "last_week_ending": "2026-07-19", "weeks": 60, "products": 186},
    "mari": {"label": "Marilyna's",  "first_week_ending": "2025-06-01",
             "last_week_ending": "2026-07-19", "weeks": 60, "products": 505}
  },
  "endpoints": {
    "index": "https://app.stowawaybar.com/sales/products/index.json",
    "stow":  "https://app.stowawaybar.com/sales/products/rollup_stow.json",
    "hg":    "https://app.stowawaybar.com/sales/products/rollup_hg.json",
    "mari":  "https://app.stowawaybar.com/sales/products/rollup_mari.json",
    "schema_doc": "https://github.com/zakstowaway/mari-daily-reporting/blob/main/dashboard/sales/products/SCHEMA.md"
  },
  "notes": "…"
}
```

### `index.json`

Lean index — one row per (venue, product) with lifetime totals only. Use to
discover what products exist and their reporting group; fetch a rollup file
for weekly detail.

```json
{
  "generated_at": "…",
  "coverage": { … },
  "product_count": 1192,
  "products": [
    {
      "venue": "stow",
      "name": "Arancini Balls [5pc]",
      "reporting_group": "Small Plates",
      "first_week_ending": "2026-06-07",
      "last_week_ending": "2026-07-19",
      "lifetime_qty": 156.0,
      "lifetime_revenue_ex_gst": 3389.88
    },
    …
  ]
}
```

Sorted by lifetime revenue descending, so the top of the list is the biggest
sellers group-wide.

### `rollup_<venue>.json`

Full weekly history for every product at one venue.

```json
{
  "venue": "stow",
  "venue_label": "Stowaway",
  "generated_at": "…",
  "coverage": { … },
  "notes": "…",
  "products": [
    {
      "name": "Arancini Balls [5pc]",
      "reporting_group": "Small Plates",
      "first_week_ending": "2026-06-07",
      "last_week_ending": "2026-07-19",
      "lifetime_qty": 156.0,
      "lifetime_revenue_ex_gst": 3389.88,
      "avg_price_ex_gst": 21.73,
      "weekly": [
        {"we": "2026-06-07", "sales_ex": 663.84, "qty": 31.0},
        {"we": "2026-06-14", "sales_ex": 630.97, "qty": 31.0},
        …
      ]
    },
    …
  ]
}
```

`weekly` is sorted ascending by week_ending. Products are sorted by lifetime
revenue descending, so the biggest sellers at that venue come first.

## Usage recipes (for Claude in any chat)

**"How many arancini balls have we sold since launch?"**

1. `mcp__workspace__web_fetch("https://app.stowawaybar.com/sales/products/index.json")`
2. Filter `.products` where `name` contains "arancini" and `venue == "stow"` → find the record
3. Report `lifetime_qty`, `lifetime_revenue_ex_gst`, and the date range.

**"Weekly arancini sales chart since launch"**

1. `mcp__workspace__web_fetch("https://app.stowawaybar.com/sales/products/rollup_stow.json")`
2. Find product `"Arancini Balls [5pc]"` → use its `weekly` array as the data source.

**"What are the top 10 products at Harry Gatos this year?"**

1. Fetch `rollup_hg.json`.
2. For each product, sum `weekly[?we >= "2026-01-01"].sales_ex`. Sort. Take top 10.

**"How much have I sold of San Giorgio Rosso di Montalcino?"**

1. Fetch `index.json`, search `.products[]` where `name` contains "san giorgio".
2. The record's `lifetime_qty` and `lifetime_revenue_ex_gst` answer directly. If
   weekly breakdown needed, fetch the matching `rollup_<venue>.json` and read
   that product's `weekly` array.

## Product naming & size variants

Some products appear multiple times with size suffixes (e.g. "Kuku Sauvignon
Blanc — Regular" vs "— Large" vs "— Bottle"). These are separate records in
the index. If the user asks "how much Kuku Sauvignon did we sell", sum across
all three size variants — the upstream build script (`build_products_weekly.py`)
does *not* auto-merge size variants for the API, because they carry different
prices and pour sizes and should stay distinguishable.

Whitelisted size suffixes handled elsewhere in the pipeline (for other reports):
Pint, Schooner, Regular, Large, Bottle, Glass, Regular Glass, Large Glass.

## Attribution

- **Stowaway till** rings up bar, kitchen, and Marilyna's product. The pipeline
  splits the till receipts by product's mapped department (`stow` / `mari` /
  `hgf`) and attributes revenue accordingly. `rollup_stow.json` contains only
  Stowaway-attributed products.
- **Harry Gatos till** rings up HG products plus a Stowaway food subset (`stf`)
  that's re-attributed to Stow. `rollup_hg.json` contains only HG-attributed
  products.
- **Marilyna's** has no till. `rollup_mari.json` is the `m`-attributed slice of
  the Stowaway till.

Full attribution rules live in `scripts/product_dept_map.json` and the
`HANDOFF_2026-07-16.md` sections 2 and 9.

## Freshness

`generated_at` reflects when `build_products_api.py` last ran. This is wired
into `.github/workflows/daily_pull.yml` after the `build_products_weekly.py`
step, so the API refreshes automatically on every daily pull.

If a file's `generated_at` is more than 30 hours stale, check:
- `.github/workflows/daily_pull.yml` run status
- Whether `data/products_weekly.csv` itself is stale (upstream)

## Change log

- **2026-07-22** — Initial deploy. Serves lifetime + weekly per venue.
- Coming soon (Path C): a Cowork/Claude plugin wrapping these endpoints in a
  proper MCP so callers can ask `sales.product_history("name")` instead of
  fetching + parsing JSON.
