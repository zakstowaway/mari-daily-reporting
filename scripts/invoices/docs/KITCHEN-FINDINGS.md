# Kitchen suppliers — first pass

Bar work validated ~$820k of ILG. Kitchen is a comparable pile of money with
**zero** validation until now:

| Supplier | Lifetime spend | Invoices | Dext line items |
|---|---|---|---|
| Foodlink Australia | $819,412 | 2,092 | 6/6 ✅ |
| Torino Food Service | $507,605 | 393 | 6/6 (inactive since 2024) |
| Andrews Meat Industry | $338,393 | 1,383 | 6/6 |
| **Select Fresh Providores** | **$302,013** | **2,852** | **0/6** ❌ |
| **Gulli Food Distributors** | **$298,130** | 253 | **0/6** ❌ |
| B&E Foods | $63,952 | 126 | intermittent |
| M & J Chickens | $75,401 | 447 | 0/6 |

Two invoices read so far. Two traps found. Both silent, both expensive.

---

## TRAP 1 — Venue signals are PER-SUPPLIER (Select Fresh)

Two real invoices, **same supplier, same day** (15 Jul 2026):

| | inv 3084903 | inv 3085647 |
|---|---|---|
| Invoice To | `HARRY GATTOS` *(sic)* | `STOWAWAY` |
| Address | `LVL 1, SHP 18, 1-3 MOORE ROAD` | `LVL 1, SHP 18, 1-3 MOORE ROAD` |
| Delivery code | `182096#` | `182096#` |
| Delivery Instructions | **`BAR AT STOWAWAY`** | — |
| **Account Code** | **`HARGAT`** | **`STOWA`** |
| Truth | Harry Gatos | Stowaway |

**Appendix A resolves the first one to Stowaway. It's a Harry Gatos invoice.**

- The **address is identical on both venues**. Appendix A's
  `Shop 18/1-3 Moore Rd = Stowaway` fires on Harry Gatos invoices.
- **`182096#` is on both.** Appendix A calls it Harry Gatos. It's Select Fresh's
  *group* account ref. That fact is true for ILG's numbering and false here.
- The HG invoice literally reads **"BAR AT STOWAWAY"** — HG is upstairs, produce
  drops at the bar. Text matching on venue names resolves it **backwards**.
- Only **Account Code** (`HARGAT` / `STOWA`) discriminates.

**Lesson: venue signals do not transfer between suppliers.**
ILG uses numeric account codes (2428 / 3622). Select Fresh uses alpha
(STOWA / HARGAT). Applying one supplier's rule to another silently misroutes.

**Cost:** stock lands in the wrong venue, and per Appendix A a cost update
against the wrong venue's ProductIDs **silently does nothing**. Blast radius:
$302,013 across 2,852 invoices.

Fixed: `venue_resolution.by_supplier`, with `ignore_signals` for the three
misleading fields. 9 regression tests.

---

## TRAP 2 — B&E qty is in UOM units, and UOM is per-line

Invoice 6969915 (16 Jul 2026, Stowaway):

```
Item Code  Description                              Ordered Shipped UOM  Ship Doc+Unit  Price   Line Total
18484      CANNED - ANCHOVY FILLETS 690G(12)         1.00   1.00   UNIT   0.08 CTN     $18.00    $18.00
12776      CHICKEN BREAST (F) SLICE 5MM PREM 5KG BAG 5.00   5.00   KG     1.00 BAG     $12.20    $61.00
19626      SAUSAGE - MILD SPANISH CHORIZO 1KG (15)   1.00   1.00   KG     0.07 CTN     $13.70    $13.70
28087      CHILLI - FLAKE / CRUSHED 1KG CSI          1.00   1.00   BAG    0.10 CTN     $13.70    $13.70
17723      YOGHURT - GREEK 2KG PROCAL                1.00   1.00   TUB    1.00         $14.50    $14.50
11605      ANTIPASTO - CHARGRILLED EGGPLANT 2KG(4)   1.00   1.00   UNIT   0.25 CTN     $21.90    $21.90
```

**`CHICKEN BREAST ... 5KG BAG`, Shipped `5.00`, UOM `KG`, Ship Doc `1.00 BAG`.**

That is **ONE 5kg bag**. The qty column is *weight*, because the line's UOM is KG.
Read `5.00` as a count and you book **5 bags / 25kg** of chicken that was never
delivered — a **5× stock and COGS error**, silent, and it looks perfectly normal.

`UOM` varies **per line**: `UNIT`, `KG`, `BAG`, `TUB` all on one invoice. There
is no single reading of the qty column. The `Ship Doc + Unit UOM` column carries
the actual physical pack count (`1.00 BAG`).

### Appendix B is CONFIRMED here

- `qty × Item Price = Line Total` — holds 6/6. `LINE_ARITHMETIC` works on B&E.
- The `(N)` in the description IS units-per-carton:
  `690G(12)` → `0.08 CTN` (1/12 ✓) · `1KG (15)` → `0.07` (1/15 ✓) ·
  `2KG TUB (4)` → `0.25` (1/4 ✓)
- Per-KG rule works: `$12.20/KG × 5kg = $61.00` = the stated Line Total.
  **But the invoice already did the multiplication.** Applying Appendix B's
  `KG_price × pack_weight` on top of a Line Total that already reflects it is a
  double-count waiting to happen. Prefer Line Total; derive units from
  `Shipped ÷ pack_weight`.

---

## Structural differences from liquor (affect the extractor)

| | Liquor (ILG) | Kitchen |
|---|---|---|
| GST | 10% everywhere | **GST-free** (Foodlink/Select Fresh/B&E/Andrews all $0.00 tax) |
| Mixed tax | no | **yes — Gulli CI-424608: $5.84 tax on $352.26** (≈$64 taxable, rest free) |
| Qty semantics | cases / `0/N` repacks | **per-line UOM** — KG, BAG, TUB, UNIT, BUNCH, PKT |
| Fractional qty | no | **yes — Select Fresh `CARROT KG 0.50`** |
| Ordered vs shipped | one Qty column | **two columns** (Order/Supply, Ordered/Shipped) → short-ships are explicit |
| Pages | 1 | **B&E 6969915 is `Page 1 of 2`** — multi-page extraction needed |
| Price volatility | stable | fresh produce moves weekly — price-change flagging must be suppressed |

### Gulli — the mixed-tax case, still unread

`CI-424608`, $352.26 total, **$5.84 tax**. That's not 1/11 ($32.02) — only ~$64
of the invoice is taxable. My `GST_MISMATCH` check only fires when *every* line
is taxable, so it won't false-positive here — but per-line tax treatment is
untested against a real mixed invoice. $298,130 of spend behind it.

---

## Still unvalidated

- **Foodlink** — $819,412, the single biggest kitchen supplier. Not read.
- **Andrews Meat / M&J** — catch-weight risk. If they bill actual kilos against
  a counted order, `qty × unit = total` may legitimately fail and
  `LINE_ARITHMETIC` would false-positive on *correct* invoices. That's the
  failure mode that teaches people to ignore alarms. Must check before shipping.
- **Gulli** — mixed tax, unread.
- **Select Fresh page 2+** — multi-page behaviour unverified.
