"""
Supplier invoice extraction, validation and COGS.

    email → Pipedream → repository_dispatch → extract → validate → data/

Design note — why there's a model in the loop:

    There is no trustworthy structured source for line items. Dext holds NONE
    for any liquor supplier (ILG: 0/40 over five years, $821k of spend), its
    LineItem type has no quantity field at all, and where it DOES extract it
    gets things wrong (Foodlink: records $0.00 GST on invoices that print
    $4.60 on their face). Sun Circle's invoices are handwritten.

    So the document is the only source. That is what the Claude call buys.

    The model NEVER has the final say. It proposes numbers; validator.py —
    pure arithmetic, no model — decides whether they're allowed through.
    See docs/FINDINGS.md.

Layout:
    models.py       the canonical Invoice/InvoiceLine shape (Decimal, never float)
    validator.py    THE GATE. Arithmetic only. PASS or REVIEW, never a shrug.
    suppliers.yaml  supplier rules — the ONLY layer that changes
    EXTRACTION.md   the extractor spec. This IS the prompt.
    extract.py      PDF bytes → Invoice, via the Anthropic API
    dext_client.js  Dext GraphQL — BACKFILL ONLY, needs a browser session
    tests/          183 tests
    docs/           evidence for every rule in suppliers.yaml
"""

from modules.invoices.models import CostBasis, Invoice, InvoiceLine, LineClass, TaxTreatment, Venue
from modules.invoices.validator import Finding, Severity, Status, ValidationResult, Validator

__all__ = [
    "Invoice", "InvoiceLine", "Venue", "LineClass", "TaxTreatment", "CostBasis",
    "Validator", "ValidationResult", "Finding", "Status", "Severity",
]
