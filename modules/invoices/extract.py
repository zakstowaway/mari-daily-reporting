"""
PDF bytes -> Invoice, via the Anthropic API.

This is the ONLY part of the pipeline that calls a model, and it is
deliberately dumb: it hands Claude the PDF plus EXTRACTION.md plus
suppliers.yaml, and asks for JSON. It makes no decisions.

Everything that DECIDES is downstream in validator.py, which is pure
arithmetic and never calls a model.

    extract()  proposes
    validate() disposes

Cost: ~1-3c per invoice. ~80 invoices/week => ~$40-125/year.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from modules.invoices.models import CostBasis, Invoice, InvoiceLine, LineClass, TaxTreatment, Venue

HERE = Path(__file__).parent
MODEL = os.environ.get("INVOICE_MODEL", "claude-haiku-4-5")
MAX_TOKENS = 8000


class ExtractionError(RuntimeError):
    pass


def _load_prompt() -> str:
    """
    EXTRACTION.md IS the extractor. suppliers.yaml is the supplier knowledge.
    Both go in the prompt every call — there is no fine-tuned model, and
    nothing is remembered between runs. If a rule isn't in these two files,
    it does not exist.
    """
    spec = (HERE / "EXTRACTION.md").read_text()
    rules = (HERE / "suppliers.yaml").read_text()
    return (
        f"{spec}\n\n"
        f"---\n\n"
        f"# config/suppliers.yaml (the supplier rules referenced above)\n\n"
        f"```yaml\n{rules}\n```\n"
    )


_SCHEMA_HINT = """
Return ONLY a JSON object, no prose, matching:

{
  "supplier_key": "ilg",                 // key from suppliers.yaml, or best guess
  "supplier_name_raw": "...",            // verbatim from the document
  "invoice_ref": "03729959",
  "invoice_date": "2026-07-14",          // ISO
  "total_incl": "2283.19",               // string, GST-INCLUSIVE
  "venue": "stowaway",                   // stowaway | harry_gatos | unknown
  "account_code": "2428",                // whatever venue signal you used
  "po_refs": ["54361209"],
  "gst_total": "207.56",
  "wet_total": null,
  "lines": [
    {
      "description": "APEROL",           // verbatim
      "supplier_code": "395-6785P",      // CRITICAL — resolution keys on this
      "qty": "1",
      "pack_size": 6,
      "unit_price_incl": "29.0817",      // DERIVED: line_total_incl/(qty*pack_size)
      "line_total_incl": "174.49",
      "line_class": "stock",             // stock | extra | wos | unknown
      "tax_treatment": "gst",            // gst | gst_free | wet
      "cost_basis": "per_bottle",
      "raw_qty": "1",
      "raw_uom": null,
      "notes": []
    }
  ]
}

All money as STRINGS (parsed as Decimal — never float).
If you cannot classify a line, use "unknown". Do NOT guess.
If you cannot resolve the venue, use "unknown". Do NOT guess.
"""


def extract(pdf_bytes: Optional[bytes] = None, *, text: Optional[str] = None,
            client=None, filename: str = "invoice.pdf") -> Invoice:
    """
    Ask Claude to read the invoice. Returns a parsed (UNVALIDATED) Invoice —
    always pass it through Validator.validate() before trusting it.

    CHEAPEST-FALLBACK SHAPE (this only runs when a deterministic parser can't):
      * model defaults to Haiku 4.5 ($1/$5 per M) — see MODEL.
      * the big static rules block (EXTRACTION.md + suppliers.yaml + schema) is
        identical every call, so it is sent FIRST and prompt-cached
        (cache_control ephemeral) → ~10% cost after the first call. The variable
        part (this invoice) comes after the cache breakpoint.
      * prefer `text` (already pulled from the PDF's text layer, cheap tokens)
        over the PDF `document` block (image tokens). run.py passes text when it
        has it; the raw PDF is the fallback-of-the-fallback.
    """
    if client is None:
        try:
            from anthropic import Anthropic
        except ImportError as e:  # pragma: no cover
            raise ExtractionError("pip install anthropic") from e
        client = Anthropic()  # reads ANTHROPIC_API_KEY

    # Static, cacheable prefix — everything up to and including this block is the
    # cache key; it never varies, so subsequent invoices reuse it.
    content = [{"type": "text", "text": _load_prompt() + _SCHEMA_HINT,
                "cache_control": {"type": "ephemeral"}}]
    # Variable suffix — the actual invoice, as cheap text when we have it.
    if text:
        content.append({"type": "text", "text": f"\n\n=== INVOICE TEXT ===\n{text}"})
    elif pdf_bytes is not None:
        content.append({"type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf",
                                   "data": base64.b64encode(pdf_bytes).decode()}})
    else:
        raise ExtractionError("extract() needs pdf_bytes or text")

    resp = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": content}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    return parse(raw, source=filename)


def parse(raw: str, *, source: str = "") -> Invoice:
    """Parse the model's JSON into an Invoice. Strict — no coercion, no guessing."""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1]
        if txt.startswith("json"):
            txt = txt[4:]
    try:
        d: dict[str, Any] = json.loads(txt)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"model did not return JSON: {e}") from e

    def dec(v: Any) -> Optional[Decimal]:
        if v is None or v == "":
            return None
        try:
            return Decimal(str(v))
        except InvalidOperation as e:
            raise ExtractionError(f"not a number: {v!r}") from e

    def enum(cls, v, default):
        if v is None:
            return default
        try:
            return cls(v)
        except ValueError:
            # An unrecognised value must NOT silently become a sensible default.
            # Fall to the 'unknown' member where one exists so the validator
            # blocks it, rather than inventing a classification.
            return default

    lines = []
    for L in d.get("lines", []):
        lines.append(InvoiceLine(
            description=L.get("description", ""),
            qty=dec(L.get("qty")) or Decimal("0"),
            line_total_incl=dec(L.get("line_total_incl")) or Decimal("0"),
            unit_price_incl=dec(L.get("unit_price_incl")),
            unit_price_ex=dec(L.get("unit_price_ex")),
            pack_size=L.get("pack_size"),
            line_class=enum(LineClass, L.get("line_class"), LineClass.UNKNOWN),
            tax_treatment=enum(TaxTreatment, L.get("tax_treatment"), TaxTreatment.GST),
            cost_basis=enum(CostBasis, L.get("cost_basis"), CostBasis.UNKNOWN),
            gst_amount=dec(L.get("gst_amount")),
            wet_amount=dec(L.get("wet_amount")),
            supplier_code=L.get("supplier_code"),
            raw_qty=L.get("raw_qty"),
            raw_uom=L.get("raw_uom"),
            notes=L.get("notes") or [],
        ))

    total = dec(d.get("total_incl"))
    if total is None:
        raise ExtractionError("no total_incl")

    return Invoice(
        supplier_key=d.get("supplier_key") or "",
        supplier_name_raw=d.get("supplier_name_raw") or "",
        invoice_ref=d.get("invoice_ref") or "",
        invoice_date=date.fromisoformat(d["invoice_date"]) if d.get("invoice_date") else date.today(),
        total_incl=total,
        lines=lines,
        venue=enum(Venue, d.get("venue"), Venue.UNKNOWN),
        po_refs=d.get("po_refs") or [],
        subtotal_ex=dec(d.get("subtotal_ex")),
        gst_total=dec(d.get("gst_total")),
        wet_total=dec(d.get("wet_total")),
        account_code=d.get("account_code"),
        source_pdf=source,
        extractor_version=MODEL,
    )
