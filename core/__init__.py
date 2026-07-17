"""
Shared truth. Depends on nothing; everything may depend on it.

    venues.py   venue keys, OU -> dept, SUPER_RATE. The answer to "which venue".
    domain.py   identity (Purchasable / Ingredient) and time (CostSeries.as_of).

Rule: if something in here needs to import a module, it doesn't belong in here.
"""
