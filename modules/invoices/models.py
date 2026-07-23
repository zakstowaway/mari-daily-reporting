"""
Canonical invoice shape.

This is the contract between the extractor (LLM, may be wrong) and everything
downstream (Lightspeed receives, COGS mapping, price history). Nothing reaches
the database without passing through validator.validate().

Money is Decimal throughout. Never float. A cent of drift compounds into a
reconciliation failure that costs an hour to chase.

All prices GST-INCLUSIVE unless the field name says _ex. Lightspeed cost prices
are GST-inclusive (CostTaxCode = GST), so incl is the native basis here and
ex-GST is the derived one. This is the opposite of most invoice formats, which
is exactly why extraction gets it wrong and why we check the maths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional


class Venue(str, Enum):
    STOWAWAY = "stowaway"
    HARRY_GATOS = "harry_gatos"
    MARILYNAS = "marilynas"
    UNKNOWN = "unknown"


class LineClass(str, Enum):
    """What a line on the supplier invoice actually is."""

    STOCK = "stock"
    # Freight, fuel levy, surcharge, min-order top-up, CC surcharge.
    # Standing directive: these are NEVER entered on a Lightspeed receive.
    # They are logged to price-history with direction=delivery_fee_rolled_in.
    # The LS receive total is therefore EXPECTED to fall short of the invoice
    # total by exactly the sum of these. That gap is the green light.
    EXTRA = "extra"
    # Waiting on Stock / back-ordered. Receive qty 0, price untouched.
    WOS = "wos"
    # Extractor could not classify. Always forces review.
    UNKNOWN = "unknown"


class TaxTreatment(str, Enum):
    GST = "gst"           # 10% — most liquor, packaged goods
    GST_FREE = "gst_free"  # basic food — most of B&E, Fresh Fruit Team
    WET = "wet"           # wine: WET applies, then GST on (ex + WET)


class CostBasis(str, Enum):
    """
    How Lightspeed stores the cost for this product type.

    Getting this wrong is the single most expensive extraction error, because
    it is silent: Heaps Normal at $64.07 (case total) into a per-tin field is
    24x too high and nothing complains. Hence the sanity bounds in the config.
    """

    PER_BOTTLE = "per_bottle"
    PER_KEG = "per_keg"
    PER_CAN = "per_can"
    PER_UNIT = "per_unit"
    PER_KG = "per_kg"
    UNKNOWN = "unknown"


@dataclass
class InvoiceLine:
    description: str
    qty: Decimal
    line_total_incl: Decimal

    unit_price_incl: Optional[Decimal] = None
    unit_price_ex: Optional[Decimal] = None

    # Pack size where the line is a case/crate. A "Crates of 24" product in
    # Lightspeed expects the PER-CAN price — LS multiplies by pack size itself.
    # Enter the line total and you are 24x out. See CostBasis above.
    pack_size: Optional[int] = None

    line_class: LineClass = LineClass.UNKNOWN
    tax_treatment: TaxTreatment = TaxTreatment.GST
    cost_basis: CostBasis = CostBasis.UNKNOWN

    gst_amount: Optional[Decimal] = None
    wet_amount: Optional[Decimal] = None

    supplier_code: Optional[str] = None   # e.g. ILG code, IWI "GHEMILL-24"
    raw_qty: Optional[str] = None         # e.g. ILG "0/1" before normalisation
    raw_uom: Optional[str] = None         # e.g. B&E "KG" / "CTN"

    # Set by the resolver, not the extractor.
    lightspeed_product_id: Optional[str] = None
    lightspeed_product_name: Optional[str] = None

    notes: list[str] = field(default_factory=list)

    @property
    def is_stock(self) -> bool:
        return self.line_class == LineClass.STOCK


@dataclass
class Invoice:
    supplier_key: str          # canonical key into suppliers.yaml
    supplier_name_raw: str     # as it appeared on the document
    invoice_ref: str
    invoice_date: date
    total_incl: Decimal

    lines: list[InvoiceLine] = field(default_factory=list)

    venue: Venue = Venue.UNKNOWN
    po_refs: list[str] = field(default_factory=list)  # Bacchus can carry two

    subtotal_ex: Optional[Decimal] = None
    gst_total: Optional[Decimal] = None
    wet_total: Optional[Decimal] = None

    account_code: Optional[str] = None   # ILG 2428 / 3622 -> venue
    source_pdf: Optional[str] = None     # provenance: path/hash of the original
    extractor_version: Optional[str] = None

    @property
    def stock_lines(self) -> list[InvoiceLine]:
        return [l for l in self.lines if l.line_class == LineClass.STOCK]

    @property
    def extra_lines(self) -> list[InvoiceLine]:
        return [l for l in self.lines if l.line_class == LineClass.EXTRA]

    @property
    def extras_total(self) -> Decimal:
        return sum((l.line_total_incl for l in self.extra_lines), Decimal("0"))

    @property
    def expected_ls_receive_total(self) -> Decimal:
        """
        What the Lightspeed receive should total: stock only, extras excluded.
        If the receive matches this, the receive is correct even though it
        does NOT match the invoice total.
        """
        return sum((l.line_total_incl for l in self.stock_lines), Decimal("0"))
