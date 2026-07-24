"""
Read the payment due date straight off each invoice — per invoice, from what the
supplier actually printed. Xero history is misleading here (Zak: "we've been
paying more promptly than our terms require"), so the due date is never inferred
from how fast we paid; it's the supplier's own figure.

Every supplier states it, just differently:
  * an explicit date, inline   — B&E, Foodlink   ("Due Date: 14/05/2026")
  * an explicit date, columnar — ILG, Lion, Fresh Fruit Team (label and value in
    different table cells; only the coordinate rows put them back together)
  * terms in words             — Select Fresh "14 days", Foodlink "7 Days from
    Inv Date", Jun Pacific "C.O.D.", Gulli "30 Days From End of Invoice Month"

read_due(pdf_bytes, invoice_date) tries all of these and returns a date.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Optional

from modules.invoices import pdf_text

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
# a date as one token: 14/05/2026, 9-JUN-2026, 2026-05-14
_TOK = re.compile(r"^\d{1,2}[/-][A-Za-z0-9]{1,4}[/-]\d{2,4}$|^\d{4}-\d{2}-\d{2}$")


def _yr(y: int) -> int:
    return y + 2000 if y < 100 else y


def _one(tok: str) -> Optional[date]:
    tok = tok.strip().rstrip(".,")
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", tok)                       # ISO
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[-/]([A-Za-z]{3,})[-/](\d{2,4})$", tok)      # 9-JUN-2026
    if m and m[2][:3].lower() in MONTHS:
        try:
            return date(_yr(int(m[3])), MONTHS[m[2][:3].lower()], int(m[1]))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", tok)           # 14/05/2026 (AU d/m/y)
    if m:
        try:
            return date(_yr(int(m[3])), int(m[2]), int(m[1]))
        except ValueError:
            return None
    return None


def _three(a: str, b: str, c: str) -> Optional[date]:
    # "28 Apr 2026"
    if a.isdigit() and b[:3].lower() in MONTHS and re.match(r"^\d{2,4}$", c):
        try:
            return date(_yr(int(c)), MONTHS[b[:3].lower()], int(a))
        except ValueError:
            return None
    return None


def _eom(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _from_terms(text: str, inv: date) -> Optional[date]:
    t = text or ""
    if re.search(r"\bc\.?o\.?d\.?\b|cash\s+on\s+delivery|due\s+on\s+receipt", t, re.I):
        return inv
    m = re.search(r"(\d{1,3})\s*days?\s*from\s*end\s*of\s*(?:invoice\s*)?month", t, re.I)
    if m:
        return _eom(inv) + timedelta(days=int(m[1]))
    m = (re.search(r"(?:terms?\s*[:\-]?\s*|net\s*)(\d{1,3})\s*days?", t, re.I)
         or re.search(r"(\d{1,3})\s*days?\s*(?:from\s*inv|from\s*invoice|from\s*date)", t, re.I))
    if m:
        return inv + timedelta(days=int(m[1]))
    return None


def _from_rows(rows, inv: Optional[date]) -> Optional[date]:
    """A date sitting in (or beside) a row whose label mentions 'due'."""
    cands = []
    for r in rows:
        toks = [t for _, _, t in r]
        low = " ".join(toks).lower()
        if "due" not in low:
            continue
        for i, t in enumerate(toks):                       # single-token date in the row
            d = _one(t)
            if d:
                cands.append(d)
        for i in range(len(toks) - 2):                     # "28 Apr 2026"
            d = _three(toks[i], toks[i + 1], toks[i + 2])
            if d:
                cands.append(d)
    # keep dates on/after the invoice date; the earliest such is the due date
    cands = [d for d in cands if not inv or d >= inv]
    return min(cands) if cands else None


def read_due(pdf_bytes: bytes, invoice_date: Optional[date]) -> Optional[date]:
    text = pdf_text.text(pdf_bytes)
    # explicit inline "Due Date: <date>"
    for m in re.finditer(r"due\s*date\s*[:\-]?\s*([0-9]{1,2}[-/][A-Za-z0-9]{1,4}[-/][0-9]{2,4})", text, re.I):
        d = _one(m[1])
        if d and (not invoice_date or d >= invoice_date):
            return d
    if invoice_date:
        d = _from_terms(text, invoice_date)
        if d:
            return d
    return _from_rows(pdf_text.word_rows(pdf_bytes), invoice_date)
