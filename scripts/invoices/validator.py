"""
The gate.

Nothing reaches Lightspeed, COGS, or price-history without passing through here.

DESIGN PRINCIPLE — the only one that matters:

    This validator does not decide whether an extraction is RIGHT.
    It decides whether it is PROVABLY CONSISTENT.

    Anything not provably consistent goes to review. It never goes to the
    database with a shrug. An invoice that fails here has cost us five minutes.
    An invoice that silently passes here with a wrong number costs us a wrong
    margin on a dish for as long as nobody notices — which, historically, is
    months.

    Therefore: when in doubt, FLAG. A high review rate is a tuning problem.
    A silent pass is a money problem.

Every check here is arithmetic or bounds. None of it depends on knowing the
supplier's layout, so a supplier nobody has ever configured still gets checked.
That is what makes "works on every invoice" a property of the SYSTEM rather
than a property of the parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from .models import CostBasis, Invoice, InvoiceLine, LineClass, TaxTreatment

# GST in Australia is 10%, so the GST component of a GST-inclusive figure is
# total/11. This holds for wine too: WET is applied first, then GST on
# (ex + WET), so total_incl = (ex + WET) * 1.1 and total_incl/11 is still GST.
GST_DIVISOR = Decimal("11")


class Severity(str, Enum):
    # Blocks the write. Full stop.
    ERROR = "error"
    # Does not block, but a human should look. Surfaced in the run summary.
    WARN = "warn"
    # Informational — expected gaps, documented exceptions.
    INFO = "info"


class Status(str, Enum):
    PASS = "pass"
    REVIEW = "review"


@dataclass
class Finding:
    code: str
    severity: Severity
    message: str
    line_index: Optional[int] = None
    expected: Optional[Decimal] = None
    actual: Optional[Decimal] = None

    def __str__(self) -> str:
        loc = f" [line {self.line_index}]" if self.line_index is not None else ""
        delta = ""
        if self.expected is not None and self.actual is not None:
            delta = f" (expected {self.expected}, got {self.actual}, "
            delta += f"diff {self.actual - self.expected:+})"
        return f"{self.severity.value.upper():5s} {self.code}{loc}: {self.message}{delta}"


@dataclass
class ValidationResult:
    status: Status
    findings: list[Finding] = field(default_factory=list)
    expected_ls_receive_total: Optional[Decimal] = None
    extras_total: Optional[Decimal] = None

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARN]

    @property
    def ok(self) -> bool:
        return self.status == Status.PASS

    def report(self) -> str:
        head = f"{self.status.value.upper()} — {len(self.errors)} error(s), {len(self.warnings)} warning(s)"
        if not self.findings:
            return head
        return head + "\n" + "\n".join(f"  {f}" for f in self.findings)


class Validator:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        tol = config.get("tolerances", {})
        self.tol_reconcile = Decimal(str(tol.get("invoice_reconcile_dollars", "0.50")))
        self.tol_gst = Decimal(str(tol.get("gst_dollars", "0.10")))
        self.tol_unit = Decimal(str(tol.get("unit_price_dollars", "0.02")))
        self.bounds = config.get("sanity_bounds", {})
        self.extras_re = [re.compile(p) for p in config.get("extras_patterns", [])]
        self.wos_re = [re.compile(p) for p in config.get("wos_patterns", [])]

    # -- classification -----------------------------------------------------

    def classify_line(self, line: InvoiceLine) -> LineClass:
        """
        Best-effort classification for lines the extractor left UNKNOWN.
        Deliberately conservative: if nothing matches, stays UNKNOWN and the
        line forces review rather than being assumed to be stock.
        """
        if line.line_class != LineClass.UNKNOWN:
            return line.line_class
        desc = line.description or ""
        if any(r.search(desc) for r in self.wos_re):
            return LineClass.WOS
        if any(r.search(desc) for r in self.extras_re):
            return LineClass.EXTRA
        return LineClass.UNKNOWN

    # -- checks -------------------------------------------------------------

    def _check_line_arithmetic(self, inv: Invoice) -> list[Finding]:
        """qty x unit_price = line_total. The most basic sanity there is."""
        out: list[Finding] = []
        for i, line in enumerate(inv.lines):
            if line.unit_price_incl is None:
                continue
            if line.line_class == LineClass.WOS:
                continue  # qty 0 by definition; maths does not apply

            # A crate line: LS stores per-unit, invoice states the case total.
            # Both readings must be checked against the stated line total.
            multiplier = Decimal(line.pack_size) if line.pack_size else Decimal("1")
            expected = (line.qty * line.unit_price_incl * multiplier).quantize(Decimal("0.01"))
            actual = line.line_total_incl.quantize(Decimal("0.01"))

            if abs(expected - actual) > self.tol_unit:
                out.append(Finding(
                    code="LINE_ARITHMETIC",
                    severity=Severity.ERROR,
                    message=(
                        f"{line.description!r}: qty {line.qty} x unit {line.unit_price_incl}"
                        + (f" x pack {line.pack_size}" if line.pack_size else "")
                        + " does not equal the stated line total"
                    ),
                    line_index=i, expected=expected, actual=actual,
                ))
        return out

    def _check_invoice_reconciles(self, inv: Invoice) -> list[Finding]:
        """
        sum(all lines) == invoice total.

        This is THE check. If an extractor drops a line, misreads a figure, or
        hallucinates one, this catches it. It is also the check Dext does not do
        for us, which is why Rule 0 and the manual reconciliation gate exist.
        """
        out: list[Finding] = []
        line_sum = sum((l.line_total_incl for l in inv.lines), Decimal("0"))
        line_sum = line_sum.quantize(Decimal("0.01"))
        stated = inv.total_incl.quantize(Decimal("0.01"))
        diff = abs(line_sum - stated)

        if diff > self.tol_reconcile:
            out.append(Finding(
                code="INVOICE_RECONCILE",
                severity=Severity.ERROR,
                message=(
                    f"Sum of {len(inv.lines)} line(s) does not reconcile to the invoice "
                    f"total. A line is likely missing or misread — do NOT receive."
                ),
                expected=stated, actual=line_sum,
            ))
        return out

    def _check_gst(self, inv: Invoice) -> list[Finding]:
        """
        GST cross-check.

        For a fully-taxable invoice, GST == total_incl / 11 (holds with WET too,
        since GST applies to ex+WET). For mixed or GST-free invoices (B&E, FFT,
        Foodlink) it must be LESS. GST exceeding total/11 is impossible and
        always means a misread.
        """
        out: list[Finding] = []
        if inv.gst_total is None:
            return out

        ceiling = (inv.total_incl / GST_DIVISOR).quantize(Decimal("0.01"))
        stated = inv.gst_total.quantize(Decimal("0.01"))

        if stated > ceiling + self.tol_gst:
            out.append(Finding(
                code="GST_IMPOSSIBLE",
                severity=Severity.ERROR,
                message="Stated GST exceeds 1/11 of the invoice total, which cannot happen",
                expected=ceiling, actual=stated,
            ))
            return out

        all_taxable = inv.lines and all(
            l.tax_treatment in (TaxTreatment.GST, TaxTreatment.WET) for l in inv.lines
        )
        if all_taxable and abs(stated - ceiling) > self.tol_gst:
            out.append(Finding(
                code="GST_MISMATCH",
                severity=Severity.WARN,
                message=(
                    "Every line is taxable but GST is not 1/11 of the total. "
                    "Either a line is actually GST-free or a figure is misread."
                ),
                expected=ceiling, actual=stated,
            ))
        return out

    def _check_extras_gap(self, inv: Invoice) -> list[Finding]:
        """
        The Lightspeed receive total is EXPECTED to fall short of the invoice
        total by exactly sum(extras) — freight, fuel levy, surcharges are never
        entered on a receive (standing directive).

        Asserting the gap turns "the totals don't match" from an alarm into a
        confirmation. Without this, every Bacchus invoice looks ~$4.95 broken.
        """
        out: list[Finding] = []
        extras = inv.extras_total
        if extras == 0:
            return out

        names = ", ".join(f"{l.description} (${l.line_total_incl})" for l in inv.extra_lines)
        out.append(Finding(
            code="EXTRAS_EXCLUDED",
            severity=Severity.INFO,
            message=(
                f"LS receive will be ${extras} under the invoice total — expected. "
                f"Skipped: {names}. Log to price-history as delivery_fee_rolled_in."
            ),
            expected=inv.expected_ls_receive_total, actual=inv.total_incl,
        ))
        return out

    def _check_unclassified(self, inv: Invoice) -> list[Finding]:
        """An unclassified line is an unknown liability. Never assume stock."""
        out: list[Finding] = []
        for i, line in enumerate(inv.lines):
            if line.line_class == LineClass.UNKNOWN:
                out.append(Finding(
                    code="LINE_UNCLASSIFIED",
                    severity=Severity.ERROR,
                    message=(
                        f"{line.description!r} could not be classified as stock, "
                        f"extra, or WOS. Refusing to guess."
                    ),
                    line_index=i,
                ))
        return out

    def _check_sanity_bounds(self, inv: Invoice) -> list[Finding]:
        """
        The silent-error net.

        A case total landing in a per-unit field reconciles perfectly and is
        still 24x wrong. Arithmetic cannot catch it; only plausibility can.
        Heaps Normal at $64.07/tin is the canonical example.
        """
        out: list[Finding] = []
        for i, line in enumerate(inv.lines):
            if line.line_class != LineClass.STOCK:
                continue
            if line.cost_basis == CostBasis.UNKNOWN or line.unit_price_incl is None:
                continue
            b = self.bounds.get(line.cost_basis.value)
            if not b:
                continue
            lo, hi = Decimal(str(b["min"])), Decimal(str(b["max"]))
            p = line.unit_price_incl
            if p < lo or p > hi:
                hint = ""
                if line.pack_size and abs(p / Decimal(line.pack_size) - lo) < (hi - lo):
                    hint = (f" Dividing by pack size {line.pack_size} gives "
                            f"${(p / Decimal(line.pack_size)).quantize(Decimal('0.01'))} "
                            f"— is this a case total in a per-unit field?")
                out.append(Finding(
                    code="SANITY_BOUNDS",
                    severity=Severity.ERROR,
                    message=(
                        f"{line.description!r}: ${p} is outside the plausible "
                        f"{line.cost_basis.value} range ${lo}-${hi}.{hint}"
                    ),
                    line_index=i,
                ))
        return out

    def _check_required_fields(self, inv: Invoice) -> list[Finding]:
        out: list[Finding] = []
        if not inv.lines:
            out.append(Finding("NO_LINES", Severity.ERROR,
                               "Invoice has no line items — nothing to receive"))
        if not inv.invoice_ref:
            out.append(Finding("NO_INVOICE_REF", Severity.WARN,
                               "No invoice reference — price-history audit trail needs one"))
        if inv.venue.value == "unknown":
            out.append(Finding("NO_VENUE", Severity.ERROR,
                               "Venue unresolved — cannot pick a product namespace. "
                               "Stowaway and Harry Gatos have different ProductIDs."))
        if inv.total_incl is None or inv.total_incl <= 0:
            out.append(Finding("BAD_TOTAL", Severity.ERROR,
                               "Invoice total missing or non-positive"))
        return out

    # -- entry point --------------------------------------------------------

    def validate(self, inv: Invoice) -> ValidationResult:
        # Fill in classifications the extractor left open, then check.
        for line in inv.lines:
            line.line_class = self.classify_line(line)

        findings: list[Finding] = []
        findings += self._check_required_fields(inv)
        findings += self._check_unclassified(inv)
        findings += self._check_line_arithmetic(inv)
        findings += self._check_invoice_reconciles(inv)
        findings += self._check_gst(inv)
        findings += self._check_sanity_bounds(inv)
        findings += self._check_extras_gap(inv)

        has_error = any(f.severity == Severity.ERROR for f in findings)
        return ValidationResult(
            status=Status.REVIEW if has_error else Status.PASS,
            findings=findings,
            expected_ls_receive_total=inv.expected_ls_receive_total,
            extras_total=inv.extras_total,
        )
