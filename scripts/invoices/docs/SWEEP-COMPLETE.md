# Supplier sweep — complete

**16 Jul 2026.** One real invoice read from **every supplier active in the last
30 days** (strict window, >= 2026-06-16, per Zak's directive).

## Coverage

| Supplier | 30d spend | Invoice read | Reconciles | Traps found |
|---|---|---|---|---|
| B&E Foods | $16,611 | 6969915 | ✅ | qty=weight (5× stock), UOM per line, multi-page |
| ILG | $11,564 | 03729959 | ✅ 14/14 | LUC varies per product, freight in line, `0/N`, names |
| Lion | $6,812 | 94755729 | ✅ 3/3 | **discount column**, freight in line, 49.5L kegs |
| Foodlink | $5,362 | SI4467596 | ✅ | **ex-GST lines vs incl-GST header**, Dext $0.00 GST |
| Gulli | $5,285 | CI-424608 | ✅ 5/5 | ex-GST, rate column, $0.00 delivery line |
| Paramount | $3,368 | 5441124 | ✅ 6/6 | **LUC per-case (10.9×)**, `Size=MISC`, `0/N` |
| Sun Circle | $3,168 | 16961 | ✅ 4/4 | **HANDWRITTEN** — Dext can't read it |
| Bacchus | $3,123 | INV494857 | ✅ 2/2 | LUC incl-WET, fuel levy sometimes absent |
| Select Fresh | $2,822 | 3084903 | ✅ 13/13 | **venue signals identical across venues** |
| FFT | $2,359 | INB00111435 | ✅ 6/6 | none — cleanest invoice in the estate |
| Nelson | $2,146 | 20039352 | ✅ 2/2 | pre-discount unit price (17% LOW) |
| Viticult | $2,131 | INV-02634 | ✅ 2/2 | no WET column, GST-free footer freight |
| Grifter | $1,872 | 82888 | ✅ | Appendix B's $34.65 keg is fiction |
| Combined | $1,853 | SINV203712 | ✅ 2/2 | **Unit Price 21.7% LOW** |
| Jun Pacific | $1,644 | NB10521714 | ✅ 4/4 | `G`/`W` letter tax codes, multi-page |

**15 suppliers. 15 invoices. 15 reconciled to the cent.**
**74 products costed** → `cogs-list.csv`, 74/74 inside sanity bounds.

## The headline

**Every supplier prints a "unit cost" column. No two mean the same thing.**

| Supplier | Column | Actually is | vs truth |
|---|---|---|---|
| ILG | `LUC ex GST` | **varies per product** | 3.6–5.5× HIGH |
| Paramount | `LUC Ex GST` | per CASE | **10.9× HIGH** |
| Lion | `UNIT VALUE` | pre-discount list | 15–32% HIGH |
| Combined | `Unit Price` | pre-disc/WET/GST | **21.7% LOW** |
| Nelson | `W/sale Price/Bot` | pre-discount | **17% LOW** |
| Bacchus | `LUC` | per btl incl WET | ~10% LOW |
| Viticult | `LUC (Ex GST)` | post-disc per btl | reliable |

Wrong in **both directions**, 17% to 10.9×, and the column name tells you
nothing. **LOW is worse** — it makes GP look better, so nobody investigates.

**One rule: `line_total_incl / (qty × pack_size)`. Derive. Never read.**

## Every PO gap now explained

| PO | Supplier | LS | Invoice | Gap | Cause |
|---|---|---|---|---|---|
| 54361219 | Lion | 1624.59 | 1624.59 | **0.00** | freight per-line — nothing to skip |
| 54361210 | Bacchus | 384.55 | 384.55 | **0.00** | no fuel levy this invoice |
| 54361212 | Combined | 583.64 | 583.64 | 0.00 | no extras |
| 54361216 | Nelson | 280.89 | 280.89 | 0.00 | no extras |
| 54361213 | Grifter | 292.05 | 297.55 | 5.50 | Freight |
| 54361217 | Viticult | 458.19 | 464.78 | 6.59 | Freight Total (GST-free) |
| 54361220 | Paramount | 237.33 | 254.38 | 17.05 | Carton Frt + MinDel + Fuel |

The two $0.00 gaps I'd flagged as suspicious are **correct**. Both open items closed.

## Wine formula — verified 6/6 across three suppliers

```
WET   = net × 0.29
GST   = (net + WET) × 0.10
Gross = net × 1.29 × 1.1 = net × 1.419
```
Discount applies to `net` BEFORE WET. Viticult is the exception — no WET column.

## Bugs found in my own work

1. **Unanchored extras regex.** `(?i)freight` matched "Frenchman's Freight Pale
   Ale [Keg]" — a real product classified as an extras line and **silently
   dropped from the receive**. All 16 patterns now anchored.
2. **A vacuous guard test.** It passed `line_class=STOCK`; `classify_line()`
   returns early on already-classified lines, so it never exercised the
   patterns. Passing while testing nothing. Now passes `UNKNOWN` + 6 adversarial names.
3. **`tot/qty` without pack size** in the ILG fixture — `SANITY_BOUNDS` caught it
   on 4 lines. The check fired on real data against its own author.

## Still open

- **code→ProductID table** — the hard blocker for automated receiving.
- **Andrews Meat catch-weight** — LOW priority ($571/30d, was $338k lifetime).
- **Viticult WET** — unverified.
- **Foodlink stock units** — `CTN-6`/`CTN-12`, carton vs single unknown.
- **Page 2** of B&E and Jun Pacific never read.
- **HG PO 35985412** — $274.98 phantom stock, Zak's decision.
- **Foodlink GST in Xero** — Donna's call, not engineering.
