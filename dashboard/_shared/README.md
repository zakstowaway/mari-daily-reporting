# Sales dashboard — module map & invariants

`app.stowawaybar.com/sales/` used to be one 194KB `index.html` with all the
business logic inline. That is why it kept regressing: a UI tweak sat inches from
the P&L maths, nothing was tested, and every change was eyeballed. It is now a
thin shell that loads four layered modules, and an **enforced** guard keeps it
that way.

## Layers (all classic `<script src>`, loaded before the inline bootstrap)

| File | Responsibility | Touches DOM? | Tested by |
|------|----------------|--------------|-----------|
| `pnl.js`    | Pure P&L maths — `pnlWindow`, overheads, delivery, wage/leave, rollups | No | `scripts/test_pnl_model.mjs` (conservation, group = Σvenues) |
| `util.js`   | Helpers, formatting, data transforms (`fmtDollars`, `synthesizeGroupHistory`, …) | No | `scripts/test_dashboard_units.mjs` |
| `data.js`   | Async loaders that fetch feeds into `STATE` | fetch only | via render test |
| `render.js` | All DOM rendering + UI event handlers | Yes | `scripts/test_dashboard_render.mjs` (drives `render()` over venue × timeframe) |

`index.html` holds only: the markup shell, the config objects (`VENUE_CONFIG`,
`ROLE_CONFIG`, `CARD_DEFS`, targets), the shared `STATE`, and the single
`bootstrap()` call. **No business logic lives in `index.html`.**

Every top-level binding is a global (functions are auto-global; config is `var`),
so cross-module references resolve exactly as when everything was inline. `STATE`
is the one shared object the model reads; the model never writes the page.

## The rule, and why it can't be broken

`scripts/arch_guard.py` runs in **`tests.yml` (every push/PR)** and gates
**`deploy_dashboard.yml` (every deploy)**. It fails the build on:

1. any function declaration inside `index.html` (logic creeping back onto the HTML)
2. `index.html` past the shell size cap
3. a missing module, or any module failing `node --check`
4. a DOM token in `pnl.js` (the model touching the page)
5. a function defined in two modules
6. a missing behaviour marker (day scrubber, leave toggle, delivery KPI, …)
7. any of the three JS test suites failing

Drift doesn't get a warning — it goes red and never ships. To add a feature: maths
in `pnl.js`/`util.js`, fetch in `data.js`, DOM in `render.js`, and add/extend a
test. If the guard is unhappy, the code is in the wrong layer.

## Data-side guard

`scripts/schema_guard.py` (daily pull + `tests.yml`) blocks the *data* regressions
that also bit us: a dropped history column, lost dates (truncation), or a column
going dark (e.g. the `leave_dollars` wipe). Fix data at source — never by
hand-editing a generated `*_daily_history.csv`, because the next rebuild
overwrites it.
