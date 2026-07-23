"""
Xero coding suggester — the Dext replacement's "what account & venue?" layer.

Given an extracted (and validated) Invoice, propose for every line the Xero GL
account it should be coded to, plus a venue/department tracking option for the
whole bill. Deterministic and transparent: a bookkeeper can read exactly why a
line landed where it did, and every rule is editable here.

Priority, highest first:
  1. line-description keyword   (freight, packaging, cleaning, glassware ...)
  2. known supplier default     (our recurring suppliers -> food vs beverage)
  3. supplier-name category     (unknown supplier -> guess from its name)
  4. fallback                   (111 Purchases Other COGS)

Nothing here writes to Xero. It only *suggests*; the account codes come from the
live chart of accounts snapshot in xero_accounts.json. GST is a tax rate in Xero,
not a line, so pure-GST reconciliation lines are returned uncoded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from modules.invoices.models import Invoice, LineClass, Venue

HERE = Path(__file__).parent
_COA = json.loads((HERE / "xero_accounts.json").read_text())
ACCOUNT_NAME = {a["code"]: a["name"] for a in _COA["accounts"]}
TRACKING = {c["name"]: c for c in _COA["tracking"]}

# --- account codes we route to (must exist in xero_accounts.json) --------------
FOOD, BEVERAGE, BAR_SUPPLIES = "115", "113", "112"
PACKAGING, CLEANING, FREIGHT = "117", "306", "342"
GLASSWARE, KITCHEN_SUPPLIES, OTHER_COGS = "313", "373", "111"
ELECTRICITY, INTERNET = "330", "368"
MEU_FEES, UBER_FEES = "1193", "1195"

# --- 1. line-description keyword overrides (win over everything) ----------------
LINE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"fuel\s*levy|freight|cartage|courier|delivery\s*fee|deliver\w*\s*charge", re.I), FREIGHT),
    (re.compile(r"metho|methylat|chemical|sanitis|detergent|degreas|bleach|dishwash|rinse\s*aid|cleaner\b|cleaning", re.I), CLEANING),
    # Only UNAMBIGUOUS packaging products — bare box/bag/tray/carton/cup are the
    # pack UNIT for produce ("Limes Tray", "Onion Bag") and must stay as food.
    (re.compile(r"pizza\s*box|packag|napkin|serviette|cling\s*wrap|glad\s*wrap|greaseproof|"
                r"paper\s*bag|takeaway\s*(container|box)|coffee\s*cup|foil\s*roll|alfoil|"
                r"stretch\s*wrap|bin\s*liner|garbage\s*bag", re.I), PACKAGING),
    (re.compile(r"glass(ware)?|tumbler|crockery|cutlery|stemware", re.I), GLASSWARE),
]

# --- 2. known suppliers (keys from suppliers.yaml) ------------------------------
SUPPLIER_ACCOUNT: dict[str, str] = {
    "select_fresh": FOOD, "foodlink": FOOD, "be_foods": FOOD,
    "fresh_fruit_team": FOOD, "gulli": FOOD, "jun_pacific": FOOD,
    "ilg": BEVERAGE, "lion": BEVERAGE, "paramount": BEVERAGE,
}

# --- 3. unknown-supplier category, by name --------------------------------------
NAME_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"me\s*&\s*u|mr\s*yum|doshii", re.I), MEU_FEES),
    (re.compile(r"uber", re.I), UBER_FEES),
    (re.compile(r"brew|brewing|wine|liquor|beer|beverage|spirit|distiller|vineyard|cellar|"
                r"winestock|grifter|philter|stone\s*&\s*wood|nelson|coca|schweppes|asahi|carlton|"
                r"lion|gateway|craft", re.I), BEVERAGE),
    (re.compile(r"meat|chicken|poultry|seafood|\bfish\b|produce|fruit|\bveg\b|greengrocer|dairy|"
                r"cheese|bakery|baker|butcher|smallgood|providore|\bfarm\b|grocer|pasta|noodle|"
                r"spice|\boil\b|torino|andrews|farmer|cookers|food", re.I), FOOD),
    (re.compile(r"packag|carton|box", re.I), PACKAGING),
    (re.compile(r"clean|chemical|hygiene|sanit", re.I), CLEANING),
    (re.compile(r"freight|courier|transport|logistics", re.I), FREIGHT),
    (re.compile(r"electric|energy|\bgas\b|momentum|alinta|origin", re.I), ELECTRICITY),
    (re.compile(r"internet|telecom|\bnbn\b|broadband|telstra|optus|aussie\s*broadband", re.I), INTERNET),
]


@dataclass
class LineCoding:
    description: str
    account_code: Optional[str]
    account_name: Optional[str]
    reason: str


@dataclass
class InvoiceCoding:
    supplier_key: str
    venue: str
    tracking_category: Optional[str]
    tracking_option: Optional[str]
    tracking_confidence: str
    lines: list[LineCoding] = field(default_factory=list)

    @property
    def primary_account(self) -> Optional[str]:
        """The account most of the invoice's value codes to (for a header hint)."""
        from collections import Counter
        c = Counter(l.account_code for l in self.lines if l.account_code)
        return c.most_common(1)[0][0] if c else None


def _account_for_line(inv: Invoice, line) -> LineCoding:
    desc = line.description or ""
    # pure-GST reconciliation lines are tax, not an expense line
    if line.line_class == LineClass.EXTRA and re.fullmatch(r"\s*gst\s*", desc, re.I):
        return LineCoding(desc, None, None, "GST is a tax rate in Xero, not a coded line")
    for pat, code in LINE_RULES:                       # 1. line keyword
        if pat.search(desc):
            return LineCoding(desc, code, ACCOUNT_NAME.get(code), f"line keyword -> {ACCOUNT_NAME.get(code)}")
    code = SUPPLIER_ACCOUNT.get(inv.supplier_key)       # 2. known supplier
    if code:
        return LineCoding(desc, code, ACCOUNT_NAME.get(code), f"known supplier '{inv.supplier_key}'")
    hay = f"{inv.supplier_name_raw} {inv.supplier_key}"  # 3. supplier-name category
    for pat, code in NAME_RULES:
        if pat.search(hay):
            return LineCoding(desc, code, ACCOUNT_NAME.get(code), f"supplier name looks like {ACCOUNT_NAME.get(code)}")
    return LineCoding(desc, OTHER_COGS, ACCOUNT_NAME.get(OTHER_COGS), "fallback — no rule matched")  # 4.


def _venue_tracking(inv: Invoice, primary_account: Optional[str]) -> tuple[Optional[str], Optional[str], str]:
    """
    Map the extracted venue to a tracking option. The 'Stowaway' category holds
    the venue/department options; HG and Marilyna's are options within it, while
    a Stowaway-billed invoice picks a department (Kitchen for food, Bar for bev).
    """
    cat = "Stowaway" if "Stowaway" in TRACKING else (list(TRACKING) or [None])[0]
    opts = {o.lower(): o for o in TRACKING.get(cat, {}).get("options", [])}

    def opt(*cands):
        for c in cands:
            if c and c.lower() in opts:
                return opts[c.lower()]
        return None

    if inv.venue == Venue.MARILYNAS:
        return cat, opt("Marilyna's Pizza", "Marilynas Pizza"), "high"
    if inv.venue == Venue.HARRY_GATOS:
        return cat, opt("Harry Gatos"), "high"
    if inv.venue == Venue.STOWAWAY:
        dept = "Bar" if primary_account in (BEVERAGE, BAR_SUPPLIES) else "Kitchen" if primary_account == FOOD else None
        return cat, opt(dept) or opt("Bar", "Kitchen"), "medium" if dept else "low"
    return cat, opt("To Be Reviewed"), "low"


def suggest_coding(inv: Invoice) -> InvoiceCoding:
    lines = [_account_for_line(inv, l) for l in inv.lines]
    coding = InvoiceCoding(
        supplier_key=inv.supplier_key, venue=inv.venue.value if hasattr(inv.venue, "value") else str(inv.venue),
        tracking_category=None, tracking_option=None, tracking_confidence="low", lines=lines)
    cat, opt, conf = _venue_tracking(inv, coding.primary_account)
    coding.tracking_category, coding.tracking_option, coding.tracking_confidence = cat, opt, conf
    return coding
