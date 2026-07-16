# Owning COGS

Design note. Zak, 2026-07-17: *"the database in lightspeed is already outdated
as we have new suppliers since the food menu was updated. i'm going to get a
way more accurate cogs number doing our own database and having it wire cogs
numbers into our app at app.stowawaybar.com"*

He's right, and the job is smaller than it sounds. This note says exactly how
small, and what it costs.

## Where the COGS number comes from TODAY

Two independent feeds, both already live:

**1. Purchase-based — `data/xero_cogs_weekly.csv`** (`xero_pull.py`)

Sums Xero accounts `Purchases - Food` / `- Beverages` / `Other` / `Packaging`
per venue per week. This is **what you bought, not what you used**. Buy 20
kegs Monday and the week spikes; sell them over three weeks and those weeks
look great. Lumpy, blind to stock movement, and it cannot go below
week/department — so it can never say which dish loses money.

It is not wrong. It is the accountant's view: correct over a year, noise over
a week.

**2. Consumption-based — the `cost` field in `top_products`**

    scripts/daily_aggregator.py:482
        "cost": row_cogs(r),

That is **read verbatim from a column in the Lightspeed Insights CSV**.
Lightspeed computes it as `Produce recipe x Average Cost Price`. We copy it
into `data/{venue}_daily_*.json`, the app reads it, done.

So the consumption pipeline **already exists end to end**. Insights ->
Pipedream -> Actions -> aggregator -> JSON -> app.stowawaybar.com. Nothing
about it needs building.

The number flowing through it is just wrong.

## How wrong — measured, Stowaway, 11 days to 2026-07-16

**11 products have revenue and a $0.00 cost:**

    $405.50  Beef Cheek                 $206.69  Beef Burger D
    $178.39  Baked Camembert            $154.49  Pepsi Max Glass
    $122.98  Fancy Pants Parmy          $ 92.76  Eggplant Parmy
    $ 79.00  Southern Squid             ...

**$1,530 of $33,460 revenue — 4.6% — is booked at 100% GP** because
Lightspeed has no recipe for it. Note *which* products: they are the food
menu. Exactly the items that changed supplier when the menu changed. Zak's
account of the problem is confirmed by the data.

**Jalapeño Marg: GP 96.6%** — $917.17 revenue on $31.59 of cost. A margarita
costing 3.4% of sell price does not exist. Real cocktail GP is ~75-80%.

Both errors run the SAME direction: they understate cost, which flatters GP.
That is the dangerous half — nobody investigates good news. (Same pattern as
the supplier unit-cost traps: Combined and Nelson are the risky ones because
they read LOW.)

## What to own, and what not to

**Do NOT try to replace Lightspeed.** It is the POS. Sales capture, the
terminal, me&u ordering — that stays, it works, and it is the one thing LS is
genuinely good at. Sales data is already arriving clean and daily.

**Own the two inputs to the cost calculation:**

| | Source | Status |
|---|---|---|
| Ingredient unit costs | supplier invoices | **DONE** — `scripts/invoices/`, 15 suppliers, cent-accurate |
| Recipes (product -> ingredients x qty) | Lightspeed Produce | **THE JOB.** Stale, incomplete, unversioned |
| Units sold per product per day | Insights | **DONE** — already in the daily JSON |

    our_cost[product] = SUM over ingredients of  recipe[product][ing] x unit_cost[ing]

Then the change to the pipeline is one line:

    scripts/daily_aggregator.py:482
    -   "cost": row_cogs(r),                  # whatever Lightspeed thinks
    +   "cost": our_cost(name, row_cogs(r)),  # ours, LS as fallback + check

The app does not change. The deploy does not change. Only the provenance of
one field.

## Why this is worth doing — three views, not one

Keep BOTH existing feeds and add ours. The **differences are the product**:

    Xero purchases        what you BOUGHT      (have)
    LS recipe cost        what LS THINKS you used   (have — and it's wrong)
    Our recipe cost       what you ACTUALLY used    (to build)

- **ours vs LS**, per product, is a free test harness on real data. Where they
  disagree, one is wrong, and we can say which — no guessing.
- **purchases vs ours**, over a period, is `stock movement + waste + theft +
  variance`. That number is invisible today and it is the one that actually
  runs a venue.

That second one is the real prize. Not a prettier GP — a waste number.

## Build order

1. **Export what Produce has.** Even stale, it is the skeleton and it tells us
   the gap. `produce.kounta.com`; see the `produce-recipe-builder` skill.
2. **Recipe schema in the repo** — `data/recipes/{venue}.yaml`, versioned, one
   file, reviewable in a diff. Product name -> [{ingredient, qty, unit}].
3. **Join recipes to invoice costs.** This is where `scripts/invoices/` pays
   off: ingredient costs are already evidenced to the cent from real invoices.
4. **Compute + compare, don't cut over.** Emit `our_cost` alongside LS's for a
   week. Every divergence gets explained before anything is switched.
5. **Cut over `daily_aggregator.py:482`.** One line, once the comparison is
   boring.
6. **Add a variance panel** — purchases vs consumption.

Start with the 11 zero-cost products. They are 4.6% of revenue at a knowably
wrong number, they are all food, and they are the whole reason Zak raised
this.

## The hard parts — stated up front

- **Recipes are real work.** ~88 distinct products at Stowaway in 11 days;
  the full menu is bigger. This is the cost of the project and there is no
  clever way around it. The invoice pipeline generalises; recipes do not.
- **Yield and waste.** A 5kg brisket does not yield 5kg of portions. Recipes
  need a yield factor or every food cost reads low.
- **Batches.** Cocktail batches and preps are recipes whose ingredients are
  recipes. Needs to nest. (`stowaway-stocktake` already breaks batches down —
  reuse that knowledge, don't re-derive it.)
- **Modifiers.** POS modifiers change cost (extra shot, no cheese). Insights
  reports them as separate lines; recipes must not double-count.
- **Ingredient identity.** The same problem `resolve.py` documents: an
  ingredient must be keyed by something stable, not a name. Our DB gets to
  choose its own key — and should, since Lightspeed's SKU field is populated
  at 3.9% (Stowaway) / 5.4% (HG), and 0/144 for HG liquor.
- **LS stock goes fiction.** If recipes live here, Lightspeed's perpetual
  stock stops being maintained. Probably already true — `stowaway-stocktake`
  exists because counts are trued up by hand — but it should be a decision,
  not a surprise.

## What this does NOT fix

Average Cost Price still drives Lightspeed's own reporting and any LS-side
stock valuation. Owning recipes here does not clean that up. If LS reports are
still used for anything that matters, they will keep disagreeing with the app
— and the app will be the correct one.
