# Architecture

How to add modules here without painting yourself into a corner.

`WORKING_HERE.md` says where to work. This says how to build. Read both.

## What this repo actually is

Not a web app. **A data pipeline with a static renderer on the end.**

    external truth          this repo                    app.stowawaybar.com
    ──────────────          ─────────                    ───────────────────
    Insights CSV  ─┐
    supplier PDF  ─┼─► Pipedream ─► repository_dispatch ─► Actions ─► data/*.json ─► Pages
    Deputy API    ─┤                                          │
    Xero API      ─┘                                          └─► commit = the audit log

Consequences that decide every design question:

- **Every number in the app is a committed file.** `git log data/` is the audit
  trail. That is a feature — keep it.
- **Actions is the only runtime.** No server. Anything needing a secret runs
  there, not in a browser.
- **The dashboard is dumb on purpose.** It fetches JSON and draws. All truth is
  computed upstream, in Python, under test.

Don't fight this shape. It's cheap, auditable, and it already works.

## The rules that actually matter here

Generic advice ("write tests", "use modules") is not the interesting part. These
are the ones this codebase has *paid for*, each with the scar to prove it.

### 1. Money is `Decimal`. Never `float`.

Non-negotiable. `0.1 + 0.2 != 0.3` and COGS is a subtraction of large similar
numbers. Parse to `Decimal` at the boundary, keep it, serialise as a string.

### 2. Every derived number gets a guard, and the guard gets a test with real data in it

The bugs this week were all derived numbers with no guard:

| bug | what it did | caught by |
|---|---|---|
| `max($5, 10%)` | `max` is OR — the $5 floor was 167% on a $3 shallot, so the guard was inert | measuring real drift |
| camembert `$364/kg` | parsed the piece size, priced the case | a sanity bound |
| tomato → chilli powder | matched on `powder`, cost agreed ($16.00 vs $16.50) | refusing to guess |
| GP 89.5% painted green | too-high GP is missing ingredients, not a win | thinking about direction |

None would have been caught by types, layering, or code review. They were caught
by **bounds on physical plausibility** and **tests containing real measured
numbers**. Do that. `assert not is_suspect(Decimal("1.47"), Decimal("1.8896"))`
is worth more than any amount of structure.

### 3. Fail toward review, never toward a plausible guess

Unresolved costs five minutes of a human. Wrong-resolved writes a bad cost that
Average Cost Price then smears over ~30 days. Asymmetric — so refuse.
`resolve.py` raises `Unresolved` rather than pick. `recipes.html` asks the chef
for a pack size rather than parse one it doesn't believe.

### 4. Errors that flatter you are the dangerous ones

Nobody investigates good news. Every serious error found this week made things
look *better*: Combined/Nelson unit costs reading LOW, LS reporting 100% GP on
Beef Cheek, Jalapeño Marg at 96.6%, my own GP badge going green at 89.5%.
**When a number improves unexpectedly, that's a bug report.** Build alarms for
the pleasant direction, not just the unpleasant one.

### 5. Evidence lives next to the rule

`suppliers.yaml` states, per supplier, what its unit-cost column means AND the
invoice that proves it. `product_map.csv` carries `source_invoice` per row.
A rule without a traceable observation is folklore, and folklore cannot be
checked when the supplier changes their terms.

### 6. Config is data, code is generic

`suppliers.yaml` is the only thing that changes when a supplier changes.
`validator.py` never does. Extraction generalises, validation is arithmetic,
commercial terms are config. Keep that seam — it is why 15 suppliers cost
roughly what 3 did.

## Layout

Already right, and worth stating so it survives:

    venues.py        THE domain module. SUPER_RATE, OU->dept, venue keys.
                     8 scripts import it. New modules import it too.
    scripts/*.py     thin CLI entry points, one job each
    scripts/<mod>/   a real package when a module outgrows one file
                     (invoices/ is the worked example: models, rules-as-config,
                      a gate, tests, docs, and its prompt)
    data/            generated. Never hand-edit. Committed = auditable.
    dashboard/       fetches data/, draws. No logic worth testing.
    .github/         Actions is the runtime.

**Adding a module:** start as `scripts/thing.py`. Promote to `scripts/thing/`
when it earns a second file. Don't build the package first — `invoices/` earned
its shape by hurting.

**Do not reorganise what works.** There is no reward for moving `xero_pull.py`
into a package. Churn on working, unwatched code is how 6am jobs die.

## The gaps, honestly

- **CI existed as of 2026-07-17 and not before.** 196 tests, nothing ran them.
  `test_pipeline.py` had been broken for months — hardcoded paths into a dead
  sandbox — and nobody knew. Fixed; `tests.yml` now runs on every push.
- **Deps are pinned as of 2026-07-17** (`requirements.txt`). Before that,
  `pip install anthropic pyyaml` inline and unpinned: an upstream release could
  change the numbers overnight with no commit.
- **`data/` grows ~4 files/day/venue.** 134 entries now, `.git` 4.1MB — fine.
  At 3 venues it's ~4k files/year. Not urgent, but decide before it's 20k:
  either roll days into monthly files, or accept it (Pages serves it fine;
  the cost is `git clone`).
- **`dashboard/index.html` is 134KB in one file.** It works. But you're adding
  modules now, and a second person editing it will conflict on every line.
  Split when the *second* page needs shared code — `recipes.html` is standalone
  precisely to defer that decision. Extract `dashboard/lib/` (auth, fetch,
  money-format) at that point, not before.
- **`users.json` auth is decorative.** SHA-256 with the salt shipped to the
  browser, checked in JS. Fine for a read-only dashboard on an obscure URL.
  **Not fine for anything that writes.** Any write path authenticates at the
  endpoint (Pipedream holds the secret); the page never holds one.

## When you add the recipe module

The shape is already implied:

    scripts/recipes/          cost(), schema, tests
    data/recipes/{venue}.yaml versioned, diffable, reviewable in a PR
    data/ingredients.json     generated from invoices — nobody maintains it
    dashboard/recipes.html    picker + calculator, writes via Pipedream

and the one line that matters:

    daily_aggregator.py:482
    -   "cost": row_cogs(r),
    +   "cost": our_cost(name, row_cogs(r)),

Run both for a week. Every divergence gets an explanation before cutover. Keep
LS's number in the JSON afterwards — a permanent second opinion is cheap, and it
is how you'd notice your own recipes going stale.
