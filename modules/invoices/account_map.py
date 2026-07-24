"""
Xero coding suggester — the Dext replacement's "what account & venue?" layer.

Given an extracted (and validated) Invoice, propose for every line the Xero GL
account it should be coded to, plus a venue/department tracking option for the
whole bill. Deterministic and transparent: a bookkeeper can read exactly why a
line landed where it did, and every rule is editable here.

Priority, highest first:
  1. line-description keyword   (freight, packaging, cleaning, glassware ...)
  2. learned from Xero history  (how this supplier has actually been coded)
  3. known supplier default     (our recurring suppliers -> food vs beverage)
  4. supplier-name category     (unknown supplier -> guess from its name)
  5. fallback                   (111 Purchases Other COGS)

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

# Empirical supplier -> account/venue/terms, learned from Xero history
# (learn_coding.py). Preferred over rule guesses when confident. Keyed by a
# NORMALISED name so "Foodlink Australia" (Xero contact) and "Foodlink Australia
# Pty Ltd" (what a parser reads off the invoice) resolve to the same record.
_LEARNED_FILE = HERE / "learned_coding.json"
_SUFFIX = re.compile(r"\b(pty|ltd|limited|inc|co|corp|corporation|the|group)\b")


def _norm(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    n = _SUFFIX.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


LEARNED: dict[str, dict] = {}
if _LEARNED_FILE.exists():
    try:
        _lj = json.loads(_LEARNED_FILE.read_text())
        LEARNED = {_norm(k): v for k, v in _lj.get("suppliers", {}).items()}
    except Exception:
        LEARNED = {}


def _learned(name: str) -> Optional[dict]:
    return LEARNED.get(_norm(name))


def _learned_account(inv) -> Optional[str]:
    """A confident (>=60%) historical account for this supplier, if we have one."""
    d = _learned(inv.supplier_name_raw)
    if d and d.get("account_code") and d.get("account_confidence", 0) >= 0.6:
        return d["account_code"]
    return None


DEFAULT_DUE_DAYS = 14


def due_days_for(supplier_name: str) -> int:
    """This supplier's payment terms in days, learned from Xero history (median
    gap between bill date and due date). Falls back to net-14 when we haven't
    seen enough of their bills. net-0 (card/direct-debit suppliers) is honoured."""
    d = _learned(supplier_name)
    if d and d.get("due_days") is not None and d.get("due_days_samples", 0) >= 3:
        return int(d["due_days"])
    return DEFAULT_DUE_DAYS

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
    for pat, code in LINE_RULES:                       # 1. line keyword (freight, packaging…)
        if pat.search(desc):
            return LineCoding(desc, code, ACCOUNT_NAME.get(code), f"line keyword -> {ACCOUNT_NAME.get(code)}")
    code = _learned_account(inv)                        # 2. learned from Xero history
    if code:
        return LineCoding(desc, code, ACCOUNT_NAME.get(code), "learned from Xero history for this supplier")
    code = SUPPLIER_ACCOUNT.get(inv.supplier_key)       # 3. known supplier default
    if code:
        return LineCoding(desc, code, ACCOUNT_NAME.get(code), f"known supplier '{inv.supplier_key}'")
    hay = f"{inv.supplier_name_raw} {inv.supplier_key}"  # 4. supplier-name category
    for pat, code in NAME_RULES:
        if pat.search(hay):
            return LineCoding(desc, code, ACCOUNT_NAME.get(code), f"supplier name looks like {ACCOUNT_NAME.get(code)}")
    return LineCoding(desc, OTHER_COGS, ACCOUNT_NAME.get(OTHER_COGS), "fallback — no rule matched")  # 5.


def _find_category(option: str) -> Optional[str]:
    for name, c in TRACKING.items():
        if option in (c.get("options") or []):
            return name
    return "Stowaway" if "Stowaway" in TRACKING else (list(TRACKING) or [None])[0]


def _learned_venue(inv) -> Optional[tuple]:
    """A supplier consistently (>=85%) coded to one (category, option) in Xero is
    coded there again, regardless of the billed-to address — e.g. Gulli -> Stowaway/
    Marilyna's Pizza, Jun Pacific -> Harry Gatos/Kitchen (NOT Stowaway/Kitchen)."""
    d = _learned(inv.supplier_name_raw)
    if d and d.get("tracking_option") and d.get("tracking_confidence", 0) >= 0.85 \
            and d.get("tracking_samples", 0) >= 3:
        return d.get("tracking_category"), d["tracking_option"]
    return None


# Produce that is clearly bar (cocktail garnishes / mixers) vs clearly kitchen.
# Used only to tell a Stowaway produce delivery apart — a bar run (limes, mint,
# cucumber, citrus, juice) from a kitchen run (onions, potatoes, lettuce). Xero
# history can't help here: those bills were entered as summary lines.
_BAR_PRODUCE = re.compile(
    r"\b(lime|lemon|mint|cucumber|chill?i|jalapen|ginger|citrus|orange|grapefruit|"
    r"berr|strawberr|raspberr|blueberr|passion ?fruit|pineapple|cranberr|celery|"
    r"juice|tonic|soda|rosemary|thyme|kaffir|lemongrass)\b", re.I)
_KITCHEN_PRODUCE = re.compile(
    r"\b(onion|potato|carrot|lettuce|tomato|mushroom|garlic|pumpkin|capsicum|spinach|"
    r"broccoli|cauliflower|zucchini|eggplant|cabbage|leek|\bbean|\bpea\b|corn|kumara|"
    r"parsnip|beetroot|fennel|shallot|rocket|kale|avocado|sweet ?potato)\b", re.I)


def _bar_produce_dominant(inv) -> bool:
    """True when the produce on the invoice is clearly a bar run, not a kitchen one."""
    bar = kit = 0
    for l in inv.lines:
        d = l.description or ""
        if _BAR_PRODUCE.search(d):
            bar += 1
        elif _KITCHEN_PRODUCE.search(d):
            kit += 1
    return bar >= 2 and bar > kit * 2      # a clear bar majority


def _venue_tracking(inv: Invoice, primary_account: Optional[str]) -> tuple[Optional[str], Optional[str], str]:
    """
    Which venue/department the bill is tracked to. Prefer how this supplier has
    consistently been coded in Xero; otherwise map the invoice's billed-to venue
    (HG / Marilyna's directly; a Stowaway-billed bill picks Kitchen for food, Bar
    for beverage).
    """
    learned = _learned_venue(inv)
    if learned:
        lcat, lopt = learned
        return (lcat or _find_category(lopt)), lopt, "high"

    # No confident history -> map the billed-to venue the way the books do:
    #   Stowaway    -> Stowaway category,   dept option (Kitchen food / Bar bev)
    #   Harry Gatos -> Harry Gatos category, dept option
    #   Marilyna's  -> Stowaway category,   'Marilyna's Pizza'
    dept = ("Bar" if primary_account in (BEVERAGE, BAR_SUPPLIES)
            else "Kitchen" if primary_account == FOOD else None)
    # a produce delivery of garnishes/mixers is a Bar run, not Kitchen
    if dept == "Kitchen" and _bar_produce_dominant(inv):
        dept = "Bar"

    def in_cat(cat, *cands):
        have = {o.lower(): o for o in TRACKING.get(cat, {}).get("options", [])}
        for c in cands:
            if c and c.lower() in have:
                return have[c.lower()]
        return None

    if inv.venue == Venue.MARILYNAS:
        return "Stowaway", in_cat("Stowaway", "Marilyna's Pizza", "Marilynas Pizza"), "high"
    if inv.venue == Venue.HARRY_GATOS:
        return "Harry Gatos", in_cat("Harry Gatos", dept, "Kitchen"), "high" if dept else "medium"
    if inv.venue == Venue.STOWAWAY:
        return "Stowaway", in_cat("Stowaway", dept, "Kitchen"), "medium" if dept else "low"
    return "Stowaway", in_cat("Stowaway", "To Be Reviewed"), "low"


def suggest_coding(inv: Invoice) -> InvoiceCoding:
    lines = [_account_for_line(inv, l) for l in inv.lines]
    coding = InvoiceCoding(
        supplier_key=inv.supplier_key, venue=inv.venue.value if hasattr(inv.venue, "value") else str(inv.venue),
        tracking_category=None, tracking_option=None, tracking_confidence="low", lines=lines)
    cat, opt, conf = _venue_tracking(inv, coding.primary_account)
    coding.tracking_category, coding.tracking_option, coding.tracking_confidence = cat, opt, conf
    return coding
