"""
Modules. Each owns a capability: its pipeline, its pages, its tests.

    invoices/   supplier PDF -> validated line items -> cost observations
    recipes/    ingredient list (from invoices) + the chef UI

Rules (MODULES.md):
  * modules never import each other. They meet in data/.
  * everything may import core/. core/ imports nothing.
  * a module need not have a pipeline, or an app, or a feed. A prep timer has
    only an app. That is allowed.

scripts/ is the LEGACY home for pipelines that predate this (daily_aggregator,
xero_pull, wages). They work and run unattended at 6am. They move here when they
next need a real change — not for tidiness.
"""
