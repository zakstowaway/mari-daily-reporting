"""
Deterministic, free per-supplier invoice parsers.

Try these FIRST; fall back to the LLM (extract.py) only when there's no parser
for the sender, the PDF is a scan (no text layer), or the parse doesn't
validate. Recurring suppliers have a fixed layout, so a parser reads them
exactly, for $0, and can't hallucinate a line — the validator still checks the
result reconciles, so a broken parser fails loudly, never silently.

Registered by the sender's email DOMAIN (stable; a supplier's billing domain
rarely changes, and an unknown domain simply routes to the LLM).
"""

from __future__ import annotations

from typing import Optional

from modules.invoices import pdf_text
from modules.invoices.models import Invoice

DOMAIN_TO_PARSER: dict[str, callable] = {}


def register(*domains):
    def deco(fn):
        for d in domains:
            DOMAIN_TO_PARSER[d.lower()] = fn
        return fn
    return deco


# Import parser modules so their @register decorators run.
from modules.invoices.parsers import select_fresh      # noqa: E402,F401
from modules.invoices.parsers import fresh_fruit_team   # noqa: E402,F401
from modules.invoices.parsers import foodlink           # noqa: E402,F401
from modules.invoices.parsers import be_foods           # noqa: E402,F401
from modules.invoices.parsers import ilg                # noqa: E402,F401
from modules.invoices.parsers import gulli              # noqa: E402,F401
from modules.invoices.parsers import jun_pacific        # noqa: E402,F401


def parse_pdf(pdf_bytes: bytes, sender_domain: Optional[str] = None) -> Optional[Invoice]:
    """
    Deterministic parse if we have one for this sender, else None (-> LLM).
    Returns None on any failure so the caller falls back cleanly.
    """
    fn = DOMAIN_TO_PARSER.get((sender_domain or "").lower())
    if not fn:
        return None
    if not pdf_text.has_text_layer(pdf_bytes):
        return None                       # scanned image -> LLM
    try:
        return fn(pdf_bytes)              # parsers get the bytes; they pick text vs coordinates
    except Exception:
        return None                       # parse broke -> LLM
