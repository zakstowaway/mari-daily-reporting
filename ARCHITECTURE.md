# Architecture

`WORKING_HERE.md` = where to work. This = what the system *is*, and the three
decisions everything else hangs off.

Written 2026-07-17, before the recipe/COGS modules exist, because these are the
choices that are expensive to change later. Everything else — layout, style,
which folder — is cheap and can move.

---

## The shape

Not a web app. **A pipeline of immutable facts with a dumb renderer on the end.**

    Insights CSV ─┐
    supplier PDF ─┼─► Pipedream ─► repository_dispatch ─► Actions ─► data/ ─► Pages
    Deputy API   ─┤                                          │
    Xero API     ─┘                                          └── the commit IS the audit log

No server. Actions is the only runtime. Anything holding a secret runs there.
The dashboard fetches JSON and draws; all truth is computed upstream, in Python,
under test. Don't fight this — it's cheap, auditable, and it works.

---

## Decision 1 — Identity: two layers, not one

**The problem, proven this week:** there is no usable key in Lightspeed. SKU is
populated on 3.9% of Stowaway / 5.4% of HG products and **0 of 144 HG liquor**.
Names are actively hostile — `ALEHOUSE CRISP KEG` is LS's *Summer Mid* and
`ALEHOUSE PREMIUM KEG` is the *Draught Lager*, $27.50 apart, and the intuitive
read is backwards. Cost can't break the tie either: tomato powder ($16.50) and
chilli powder ($16.00) are indistinguishable by price.

**The decision — two entities, not one:**

    Purchasable      (supplier, supplier_code)     e.g. ("Foodlink", "102689")
      the thing you BUY. Natural key, given by the invoice, stable, and exactly
      what Back Office's SKU field was for. Never invented by us.

    Ingredient       canonical id                  e.g. "squid-tube"
      the thing a RECIPE refers to. Ours. Supplier-agnostic.

    map: Purchasable ──many-to-one──► Ingredient

**Why this is the whole ballgame**, and it is precisely Zak's stated problem —
*"we have new suppliers since the food menu was updated"*:

- If recipes referenced supplier codes, **changing supplier would break every
  recipe using that item**, and the cost history would snap. You'd be back to
  re-authoring recipes every time procurement changes. That is exactly the hole
  Lightspeed is in right now.
- With the map, switching onion suppliers is **one line of config**. Every
  recipe keeps working, untouched, and the cost series stays continuous across
  the switch — because both purchasables point at the same ingredient.

Two suppliers for one ingredient is normal, not an edge case. The map is
many-to-one *on purpose*.

`build_ingredients.py` already keys on `slug(supplier-code)` — that is a
**Purchasable id**, and it should be named that. Recipes must never reference it.

---

## Decision 2 — Time: everything is effective-dated

**The bug I nearly built.** `data/ingredients.json` holds a *current* cost. A
recipe referencing it would price July's dishes at November's costs. Recompute
an old day and the number changes. **That is Average Cost Price's exact disease
— the one this project exists to escape.** ACP smears a bad receive over ~30
days; a floating cost smears every price change over all history.

**The decision:**

    costs        APPEND-ONLY observations. Never updated, never deleted.
                 (ingredient_id, observed_on, cost_per_unit, venue, source_invoice)

    recipes      effective-dated versions. Editing a recipe writes a NEW version
                 with effective_from. The old one stays.

    COGS(day) = Σ  sales(day, product)
                 × recipe_as_of(product, day)
                 × cost_as_of(ingredient, day)

`cost_as_of(ing, d)` = the most recent observation on or before `d`.

**The invariant, and it is testable:**

> Recomputing any past day gives the same answer forever.

That single property is why this is worth building. It's what makes the app
trustworthy, it's what LS cannot offer, and it should be an actual test:
recompute 2026-07-16 tomorrow, next month, next year — identical.

**You already have the fact table and are misreading it.** `cogs_list.csv` has
`invoice_date` on every row: it *is* `(ingredient, date, cost, source_invoice)`.
It's an observation log being consumed as a snapshot. Keep the log; derive the
snapshot. Every invoice line is a free, dated, evidenced price observation —
that's a gift from the invoice pipeline, don't throw the date away.

Corollary: **invoices are immutable facts.** Never mutate an extracted invoice.
Corrections are new observations, not edits.

---

## Decision 3 — Dependency direction

    facts       invoices/   sales/            immutable, dated, know nothing
       │                                      about anything above them
       ▼
    canon       ingredients/                  purchasable→ingredient, cost series
       │
       ▼
    recipes     recipes/                      references canonical ingredient ids
       │
       ▼
    derive      cogs/                         sales × recipe × cost, all as-of
       │
       ▼
    render      dashboard/                    fetches JSON, draws, no logic

**Arrows point down. Never up.** `invoices/` must never import `recipes/`. If a
lower layer needs something from a higher one, the design is wrong — pass it in.

This is why `scripts/invoices/` has no idea Lightspeed exists, and why it
generalised to 15 suppliers for roughly the cost of 3.

---

## Venue scoping — decide once, here

| entity | scope | why |
|---|---|---|
| Purchasable | **per venue** | venues buy separately, on separate accounts |
| Ingredient | **shared** | an onion is an onion |
| Cost observation | **carries venue** | as-of lookup prefers same-venue, falls back to any |
| Recipe | **per venue** | HG's margarita is not Stowaway's |
| ProductID (LS) | **per venue** | proven: 459 shared names, **0** shared IDs |

That last row is measured, not assumed. A Stowaway ProductID is meaningless in
HG. Any function touching an LS id takes a venue or it is a bug.

---

## State, and how it may change

Everything is files in `data/`, committed. That is the database and the audit
log at once. Three kinds, with different rules:

| kind | example | rule |
|---|---|---|
| **facts** | cost observations, extracted invoices | append-only; never edited |
| **canon** | `ingredients.json`, `product_map.csv` | derived; CI proves they regenerate |
| **authored** | `recipes/{venue}.yaml` | human-written; versioned; reviewed in a diff |
| **outputs** | `{venue}_daily_*.json` | derived; safe to delete and rebuild |

**Schema evolution is additive-only.** `dashboard/index.html` is deployed and
reads these files. Adding a field is safe. Renaming or removing one breaks the
live app until Pages redeploys — and a stale browser tab breaks anyway. Version
the feed if a real break is ever needed.

**If it's derived, CI must prove it regenerates** (`tests.yml` does this for
`ingredients.json`). A derived file that no longer reproduces from source is a
fossil, and every number built on it is quietly wrong.

---

## The write path (recipes) — design it before building it

The chef UI writes. That's new; everything so far has been read-only.

    browser ──POST──► Pipedream ──repository_dispatch──► Actions ──► validate ──► commit
              (secret at the endpoint, never in the page)

- **Auth is at the endpoint.** `users.json` is decorative — SHA-256 with the
  salt shipped to the browser, checked in JS. Fine for gating a read-only
  dashboard. Never put a write secret in the page.
- **Validate server-side, in Actions.** The browser is a suggestion. Same rule
  as the invoice gate: arithmetic decides, not the client.
- **Idempotency.** A retried POST must not double-write. Key on
  (venue, product, effective_from).
- **Concurrency.** Same lesson as `daily_pull.yml`: a shared queue holds ONE
  pending run, so simultaneous dispatches drop the middle one — that already
  cost a lost payload on 2026-07-15. Group per venue.
- **Edits append.** A recipe change is a new effective-dated version, never an
  in-place update. See Decision 2.

---

## The rules the code has already paid for

Not generic advice. Each has a scar:

| bug | what happened | what caught it |
|---|---|---|
| `max($5, 10%)` | `max` is OR — the $5 floor is 167% of a $3 shallot, guard inert | measuring real drift |
| camembert `$364/kg` | parsed the piece size, priced the case | a sanity bound |
| tomato → chilli powder | matched on `powder`; cost agreed to $0.50 | refusing to guess |
| GP 89.5% painted green | too-high GP means missing ingredients, not profit | thinking about direction |

None would have been caught by types, layering, or code review.

1. **Money is `Decimal`, never `float`.** COGS subtracts large similar numbers.
2. **Every derived number gets a guard; the guard's test contains real measured
   numbers.** `assert not is_suspect(Decimal("1.47"), Decimal("1.8896"))` beats
   any amount of structure.
3. **Fail toward review.** Unresolved = 5 minutes of a human. Wrong-resolved =
   a bad cost smeared over a month. Asymmetric, so refuse.
4. **Errors that flatter you are the dangerous ones.** Every serious error found
   this week made things look *better* — Combined/Nelson reading LOW, LS at 100%
   GP on Beef Cheek, my own green 89.5%. Nobody investigates good news. Alarm on
   the pleasant direction too.
5. **Evidence lives next to the rule.** `suppliers.yaml` cites the invoice that
   proves each rule. A rule without an observation is folklore.
6. **Config is data; code is generic.** `suppliers.yaml` changes; `validator.py`
   doesn't.

---

## Layout

Mostly already right — stated so it survives:

    venues.py        THE domain module. 8 importers. New modules import it too.
    scripts/*.py     thin CLI entry points, one job each
    scripts/<mod>/   a package once a module earns a second file.
                     invoices/ is the worked example: models, rules-as-config,
                     a gate, tests, docs, its own prompt.
    data/            generated + authored. The database. The audit log.
    dashboard/       fetch and draw. No logic worth testing.
    .github/         the runtime.

**Start flat. Promote when it earns it.** `invoices/` earned its shape by hurting.

**Do not reorganise what works.** No reward for repackaging `xero_pull.py`.
Churn on unwatched 6am jobs is how they die.

---

## Known gaps

- `data/` grows ~4k files/year at 3 venues. 134 now, `.git` 4.1MB — fine. Decide
  before 20k: roll into monthly, or accept (the cost is `git clone`).
- `dashboard/index.html` is 134KB, one file. Split when a **second** page needs
  shared code — `recipes.html` is standalone precisely to defer that. Extract
  `dashboard/lib/` (auth, fetch, money) at that moment, not before.
- No `as_of` anywhere yet. Decision 2 is a decision, not yet code. It must land
  **before** recipes reference costs, or the history is wrong from day one.
