"""
Pull the text layer out of an invoice PDF — free, deterministic, no API.

Foundation for the per-supplier parsers. System-generated supplier invoices
(from Xero/MYOB/accounting software) carry a real text layer, so this is exact.
A SCANNED image invoice has no text layer; text() returns near-empty and the
caller falls back to the LLM. See parsers/.
"""

from __future__ import annotations


def text(pdf_bytes: bytes) -> str:
    """All page text, newline-joined. '' if there's no text layer (scanned)."""
    import fitz  # PyMuPDF
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def has_text_layer(pdf_bytes: bytes) -> bool:
    """Enough real text to parse? If not, it's a scan — use the LLM."""
    return len(text(pdf_bytes).strip()) > 60
