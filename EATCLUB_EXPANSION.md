# EatClub — expanding to Stowaway & Marilyna's

Design note, 2026-07-18. EatClub monitoring runs today for **Harry Gatos** only
(`Daily Sales/EatClub/`, HG static page in `dashboard/eatclub.html`). Zak: extend
it to Stowaway and Marilyna's.

This says how — and why the HG method does **not** copy-paste, because of one fact
that the `mari-daily-reporting` repo already documents:

> **Marilyna's has no till. It is a brand on the Stowaway POS, separated only by
> Reporting Group** (`daily_aggregator.py`). There is no Marilyna's site in
> Lightspeed to switch to.

Everything below hangs off that.

---

## The three venues are three different measurement problems

| venue | Lightspeed | EatClub channel | can the HG window method port? |
|---|---|---|---|
| **Harry Gatos** | own site 151095 | dine-in walk-in | already live, unchanged |
| **Stowaway** | site 150764 (**shared with Mari**) | dine-in walk-in | **yes — but only via Custom Insights**, see below |
| **Marilyna's** | none — RGs on the Stow till | **takeaway / pickup** | **no** — different baseline entirely |

### Why HG's method can't be reused as-is

HG's monitor pulls `my.kounta.com/report/salesummarybyhour` for the HG site and
subtracts EatClub full bills from the 17:00–20:59 window. That works **because HG's
site == HG's venue.** For Stowaway it doesn't: `salesummarybyhour` returns *site*
hour-totals, and site 150764 is Stowaway **+** Marilyna's on one till. The endpoint
cannot separate them. So a raw Stow window is contaminated by Mari's pizza and
delivery.

---

## The unlock: Custom Insights gives hour × Reporting Group

Verified in the portal, 2026-07-18:

- **Build From → Sales Details** (the `salelines` explore) is line-level and carries
  a **sale timestamp** and a **Reporting Group** dimension — so it produces
  **hour-of-day × Reporting Group**. That is exactly the slice that separates
  Stowaway from Marilyna's *at hour grain* on the shared till.
- On `zak@stowawaybar.com` it is a hard **"Upgrade to Custom"** wall.
- Custom **is** unlocked on **`zak.britton@hotmail.com`**, and that login sees the
  Stow site (hence Stowaway **and** Marilyna's RGs). It cannot see HG — which is
  fine, HG keeps its own path. *(Decision, Zak 2026-07-18: use the hotmail login.)*
- Prebuilt Premium Reports (Individual Reporting Group Performance etc.) filter by
  RG + Site but are period totals only — **no hour dimension.** Custom is required.

### The one report to build (hotmail login, Stow site)

`salelines` explore →

- **Dimensions:** Sale Date · Sale Hour of Day · Site Name · Reporting Group Name
- **Measures:** Revenue (inc-GST) · Revenue (ex-GST) · Quantity
- **Filter:** Site = *Stowaway Bar* · Date = yesterday (rolling)
- **Schedule:** daily CSV email, subject **`Stow Hourly RG Auto`**
- **Pipedream:** new event_type **`stow-hourly-arrived`** → `repository_dispatch` →
  Actions → aggregator, same shape as the existing `Stow Daily Sales Auto` feed.
  *(Decision, Zak 2026-07-18: scheduled CSV → Pipedream, not live scraping.)*

One feed carries **both** venues — Stowaway-proper and Marilyna's fall out by
Reporting Group. It also upgrades the whole pipeline from daily to hourly grain if
we ever want it, not just EatClub.

---

## Reporting Group filters (verbatim from `daily_aggregator.py`, already normalised lowercase)

RG matching in the repo is `strip().lower()` then drop a trailing ` [harrys]`.
Replicate that before comparing.

**Marilyna's** (`MARILYNAS_RGS`, live set):

    marilyna's pizza · marilynas pizza
    marilyna's soft drinks · marilynas soft drinks
    add-ons - pizza · dine-in pizza · delivery alcohol

  plus product-name override **`$60 BANQUET` → Mari** (its RG is missing from the
  generated map; carry the override or you undercount Mari).

**Stowaway-proper** = every RG on the Stow site **except**:
  - the Marilyna's set above (→ Mari), and
  - `harry gatos food` (→ HG kitchen, rung on the Stow till).

**Stowaway dinner window** = Stowaway-proper rows where hour ∈ {17, 18, 19, 20}.

Note `dine-in pizza` and `delivery alcohol` are **Mari's**, not Stowaway's — so
Stowaway's window correctly excludes them. This is the contamination the raw
`salesummarybyhour` endpoint could not remove; Custom Insights does.

---

## Per-venue EatClub method

### Stowaway — dine-in, same shape as HG

1. EatClub portal → *Change venue* → **Stowaway Bar** → pull redemptions for the
   night (same fields as HG).
2. Window = Σ hour∈{17..20} of **Stowaway-proper** revenue (Mari & HG-food stripped).
3. `full_price_window = window − EatClub full bills that night`.
4. Compare to **pre-launch Stowaway DOW baseline**, 8 weeks before launch.
5. NO CANNIBALISATION if full-price window ≥ baseline; watch the rescue-tier logic
   exactly as HG (a deep offer on a dead night is a rescue, not cannibalisation).

Benchmarks are Stowaway's own — do **not** reuse HG's food 61 / alc 35 / non-alc
3.5 mix or its cost model.

### Marilyna's — takeaway, cannibalises delivery not walk-ins

The dine-in window is meaningless for a pickup brand. What EatClub can eat into is
**Uber Eats / own-delivery**, and the pipeline already tracks that
(`uber_eats_revenue`, driver dollars, `data/mari_daily_history.csv`).

1. EatClub portal → *Change venue* → **Marilynas Famous Pizza** → pull redemptions.
2. Baseline = **pre-launch Mari delivery revenue by DOW** (Uber Eats + own-driver),
   from `mari_daily_history.csv`.
3. The real question is **substitution, not window-cannibalisation**:
   - `total Mari off-premise = EatClub + Uber Eats + own-delivery` on the night.
   - If total ≈ delivery baseline → EatClub merely **shifted Uber → EatClub**
     (a cheaper channel for us, but not incremental covers). Flag it.
   - If total > baseline → **incremental** pickup demand. That's the win.
4. Daily grain is sufficient here; hour grain is available from the same feed if a
   dinner-rush read is wanted later.

---

## What the repo needs (I can PR these)

Consistent with `ARCHITECTURE.md` — facts flow down, everything derived is tested.

1. **`core/venues.py`** already has all three venues. This PR adds a standalone
   `scripts/eatclub/config.py` instead of editing that load-bearing module.
2. **Hourly ingest**: parse the `Stow Hourly RG Auto` CSV → emit
   `data/stow_hourly_{date}.json` and `data/mari_hourly_{date}.json`, split by the
   RG sets. New Pipedream event + Actions step. *(Pending a real CSV sample.)*
3. **EatClub module in-repo**: per-venue transaction master, contribution, and a
   per-venue `dashboard/eatclub_{venue}.html` beside the existing HG page.
4. **Baseline builders**: a one-off **historical Custom export** (Custom allows any
   date range) gives each venue's pre-launch DOW baseline immediately.

## What needs Zak (credentials / external — I can't do these)

- **Build + schedule the Custom Insights report** on the hotmail login (spec above).
- **Create the Pipedream workflow** for `Stow Hourly RG Auto` → `stow-hourly-arrived`.
- **Confirm launch dates.** Baseline is defined as *pre-launch*; without the date the
  DOW baseline has no cutoff. (HG's was 6 May–30 Jun before its 1 Jul launch.)

## Status of the venues in EatClub (portal, 2026-07-18)

- **Stowaway Bar** — provisioned, still behind the onboarding tour → **not live yet.**
- **Marilynas Famous Pizza** — provisioned.
- Both reachable via *Change venue* under `kris@stowawaybar.com`.
