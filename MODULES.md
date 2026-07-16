# Modules

How to add the fifth thing without a rewrite.

`ARCHITECTURE.md` = the two decisions that are expensive to change (identity,
time). This = how the repo is *organised* so modules stay independent.

Written 2026-07-17, when the repo has one module and is about to have five.

---

## What changed

This repo is called `mari-daily-reporting` and it is not that any more.

It serves `app.stowawaybar.com` across three venues (Marilyna's, Stowaway,
Harry Gatos) to **six roles** — `admin, bigchef, stowfood, hgfood, bar, pizza`.
That is a platform's user list. Coming: recipe builder, COGS feed, prep timer,
"and other things".

**The prep timer is the one that breaks the old model.** Everything built so
far is `facts → derive → render`: batch jobs in Actions producing JSON. A prep
timer has no invoices, no costs, no daily pull, and no batch anything. It's a
chef at a bench hitting start. If the architecture only describes pipelines,
the prep timer has nowhere to live and gets bolted onto the dashboard — and
that's how you get a 138KB `index.html` that nobody can touch.

---

## The one idea: `data/` is the API

    pipelines/  ──write──►  data/  ◄──read──  apps/
                              ▲
                        the contract

Pipelines don't know apps exist. Apps don't know pipelines exist. They agree on
a **feed**: a file in `data/` with a documented shape.

That's the whole modularity story, and it's already true — I'm just naming it so
it survives. It means:

- A module can be a pipeline, an app, or both.
- **A module can be neither's business.** The prep timer writes no feed and
  reads no feed. That's allowed, and the structure has to say so out loud, or
  someone will invent a pointless `data/prep_timer.json` to make it "fit".
- Two modules interact **only** through a feed. Never by importing each other.

---

## Anatomy of a module

A module owns a **capability**, not a layer. It may have any of these parts;
none are mandatory:

    modules/<name>/
      pipeline/     python. runs in Actions. writes a feed.       (optional)
      app/          browser. reads a feed. or reads nothing.      (optional)
      feed.md       the contract: shape, who writes, who reads.   (if it has one)
      tests/        real numbers in them.
      README.md     what it is, in three lines.

Worked examples of the three shapes:

| module | pipeline | app | feed | note |
|---|---|---|---|---|
| `invoices` | yes | no | `costs`, `ingredients` | pure fact producer |
| `recipes` | yes (cost calc) | yes (chef UI) | reads `ingredients`, writes `recipes` | both |
| `cogs` | yes | no | reads `recipes`+`sales`, writes `cogs_daily` | pure derivation |
| `dashboard` | no | yes | reads everything | pure renderer |
| **`preptimer`** | **no** | **yes** | **none** | **pure app. Proves modules aren't all pipelines.** |

---

## Layout

    core/                 domain. Depends on NOTHING. Everything may depend on it.
      venues.py           venue keys, OU→dept, SUPER_RATE   (already exists, 8 importers)
      domain.py           identity (Purchasable/Ingredient), CostSeries.as_of
      money.py            Decimal helpers, GST            (when a 2nd module needs it)

    modules/<name>/       a capability. See anatomy above.

    apps/_shared/         the ONLY thing a prep timer shares with COGS:
      auth.js             one login, six roles, every app
      feed.js             fetch + cache-bust + "feed missing" handling
      tokens.css          the design system. --stow, --hg, --ink, .card

    data/                 THE CONTRACT. Feeds live here.
      schemas/            one per feed. CI validates against it.

    .github/workflows/    the runtime. One workflow per pipeline.

**Why `apps/_shared` matters right now, with numbers:** `index.html` (138KB),
`recipes.html` (14KB) and `eatclub.html` already exist, and each reimplements
login and fetch. A prep timer would be the fourth. Four copies of an auth check
is how one of them silently stops matching `users.json`.

---

## Rules

1. **Modules never import each other.** `recipes` must not import `invoices`.
   It reads the `ingredients` feed. If you need a module's internals, you need
   its feed instead — or the boundary is wrong.
2. **Everything may import `core/`. `core/` imports nothing.** If `core` needs
   a module, it isn't core.
3. **A feed is a published contract.** Documented shape, additive-only changes.
   `dashboard/index.html` is deployed and reading these — renaming a field
   breaks a live app and a stale browser tab. Add, don't rename.
4. **Derived feeds must regenerate in CI.** A derived file that no longer
   reproduces from source is a fossil and every number on it is quietly wrong.
   (`tests.yml` already enforces this for `ingredients.json`.)
5. **Start flat, promote when it earns it.** A module begins as one file. It
   gets a folder when it earns a second. `invoices/` earned its shape by hurting.
6. **Not every module needs a feed.** See: prep timer. Resist the urge to make
   it "fit".

---

## Migration — incremental, and safe *because CI exists now*

CI landed first on purpose. A refactor without tests is a rewrite with extra
steps; with 208 tests green on every push, moving code is provable.

**Phase 1 — `apps/_shared` (do now).** Pure addition. Nothing breaks. Port
`recipes.html` onto it — it isn't live yet, so it's the free test case. Leave
`index.html` alone.

**Phase 2 — `core/`.** Move `venues.py` + `domain.py`. Eight importers, CI
proves it in one run. Low risk, high payoff: new modules get an obvious home
for shared truth instead of copying constants.

**Phase 3 — new modules land in `modules/`.** recipes, cogs, preptimer are
greenfield. They pay no migration cost. This is why the shape is worth choosing
*today* — the modules that don't exist yet are the cheapest ones to get right.

**Phase 4 — old pipelines migrate when touched, not before.**
`daily_aggregator.py` works and runs at 6am unattended. **Do not reorganise
what works.** Churn on unwatched jobs is how they die. It moves the day it
needs a real change, and not one day sooner.

---

## Two things to decide, not for me to decide

**The repo name is now wrong.** `mari-daily-reporting` describes one venue's
reporting; it serves three venues and is becoming a platform. GitHub redirects
old URLs on rename, but the name is baked into the Pipedream dispatch URL, any
PAT scoping, and git remotes. It's cheap now and gets dearer with every module.
Renaming is your call — flagging it, not doing it.

**`users.json` auth is decorative and the platform is outgrowing it.** SHA-256
with the salt shipped to the browser, verified in JS. That's an acceptable
trade for a read-only dashboard on an obscure URL. It is *not* acceptable once
chefs are writing recipes and a prep timer is running a service. It doesn't
have to be solved today, but it must be solved before the first write path
ships, and the answer is: **auth at the endpoint, never in the page.**
