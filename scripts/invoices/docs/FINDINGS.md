# Dext survey — findings

**16 Jul 2026.** Full archive pulled via Dext's GraphQL API: **21,979 invoices,
1,033 suppliers, $9,381,857, spanning 2017-03-30 → 2026-10-05.**

---

## 1. Dext is not a line-item source. It never was.

Sample of the 400 most recent invoices (10 Jun – 16 Jul 2026):

| | invoices | spend |
|---|---|---|
| **with** line items | 102 (25%) | $26,858 (21%) |
| **without** | 298 (75%) | $99,422 (79%) |

Probing the **top 30 suppliers by spend** (6 invoices each) confirms it isn't a
recency artefact — it's structural, and it splits cleanly along food vs liquor:

| Supplier | Lifetime spend | Line items |
|---|---|---|
| Independent Liquor Group | $821,419 | **0/6** |
| Foodlink Australia | $819,412 | 6/6 |
| Uber Eats | $714,012 | 0/6 |
| Torino Food Service | $507,605 | 6/6 |
| Andrews Meat Industry | $338,393 | 6/6 |
| Select Fresh Providores | $302,013 | **0/6** |
| Paramount Liquor | $171,019 | **0/6** |
| The Grifter Brewing Co | $167,326 | 6/6 |
| Lion Beer Spirits & Wine | $121,636 | **0/6** |
| Philter Brewing | $76,478 | **0/6** |
| Nelson Wine Company NSW | $74,334 | **0/6** |
| Bacchus Wine Merchants (SB) | $68,321 | **0/6** |
| Viticult | $68,178 | **0/6** |

**Every significant liquor supplier has zero line items.** ILG is the single
largest supplier in the business — $821k across 475 invoices — and has **0/40
across five years**. Not one.

These are precisely the suppliers whose column formats Appendix B documents in
detail. That documentation was never describing Dext data. It describes the PDF
image, read visually in a browser, by a human or by Claude. Which matches the
skill's own note that the UI hides line items and pdf.js is CSP-blocked.

## 2. Dext's `LineItem` has no quantity field

The complete type:

```
id  description  totalAmount  netAmount  taxAmount
unitNetAmount  unitTotalAmount  baseTotalAmount  category
```

No `quantity`. No unit price as such. No product code. Even where line items
exist, **Dext structurally cannot express qty × unit price.** It's an accounting
split for Xero coding, not a procurement line.

This matters directly for COGS: cost-per-serve needs qty and pack size. Dext has
neither, for any supplier, ever.

## 3. Coverage changes silently over time

This is the finding that matters most, and it corrects an earlier claim of mine.

| Supplier | First invoice | First WITH line items | Still missing as late as | Sample |
|---|---|---|---|---|
| B&E Foods | 2026-03-03 | **2026-05-12** | 2026-07-01 | 19/40 |
| Foodlink | 2021-09-30 | 2022-03-30 | 2024-01-20 | 31/40 |
| ILG | 2021-09-29 | never | — | 0/40 |

Appendix B says of B&E and Fresh Fruit Team: *"Dext does NOT extract line items
for these suppliers — never has, never will. Templates aren't trained."*

**That was true when it was written.** B&E only became a supplier in March 2026,
and Dext extracted nothing for its first ten weeks. Around 12 May 2026 the
template started working. Nobody was told. The skill was amended on 18 Jun —
a month *after* the change — and still carried the old claim, because there was
no signal that anything had changed.

And it isn't a clean switch. B&E coverage is **intermittent**: 19 of 40 sampled,
with line items still absent from invoices as recent as 1 Jul 2026. Foodlink is
the same story — extracting since 2022, still dropping out as late as Jan 2024.

**This is the real argument for owning the pipeline.** Not that Dext is bad at
extraction — it's that Dext's coverage is a moving target that changes without
notice, in both directions, per supplier. You cannot build COGS on that. Not
because it's inaccurate, but because it's *unannounced*. A pipeline you own is
one whose coverage you know today and would notice changing tomorrow.

## 4. The Grifter question — settled

Appendix B's worked example:
> *"$35.00/keg with 10% discount → $31.50 ex GST → $34.65 incl GST."*

The actual invoices (4/4 have line items, so this is Dext's own data):

```
GRIFTER PALE ALE 50L KEG (KONVOY KEG)    $265.50 ex    $292.05 incl
Freight                                  $  5.00 ex    $  5.50 incl
```

Appendix K's $180–450 keg range is right. Appendix B's example is toy numbers
written to illustrate the formula and never corrected — 8.4× below reality. The
`per_keg` sanity bounds ($100–600) handle real Grifter invoices correctly and
flag the documented example, which is the desired behaviour.

Also: **Grifter freight is $5.50, not the $10–11** in the extras table.

## 5. Supplier-config corrections

| Issue | Reality |
|---|---|
| "FFT was previously Select Fresh Providores" | **Both active concurrently.** Select Fresh 2,852 invoices → 2026-07-15; FFT separate, → 2026-07-16. Two suppliers, not a rename. |
| Stone & Wood listed as a Lion alias | Was its **own supplier** (88 inv, $75k) until 2023-08-22, now via Lion. Alias is right for current invoices, wrong for history. |
| `ILG Distribution Co-Op Ltd` | **Separate entity** from Independent Liquor Group. 19 inv, $182k, → 2026-03-26. Not in config. |
| Missing from config entirely | Torino Food Service ($508k), Winestock ($209k), Gateway Liquor ($100k), Gulli ($298k), Andrews Meat ($338k), M&J Chickens, Cookers, Aquarius, Mountain Culture, 4 Pines, Cerbaco, Imbibo |
| Dext URL | Moved `gamma` → **`delta`**. Skill's URLs are stale. |

Data-quality noise worth a glance, not action: 1 invoice dated **2026-10-05**
(three months in the future), 37 under "Unknown Supplier" ($33.9k), 1 with no
supplier at all, and both "Leos Fruit & Veg" and "Leo's Wholesale Fruit & Veg"
as separate entries.

## 6. There's an API. The scraping is unnecessary.

`POST /graph/api` — GraphQL, cookie-auth, cursor pagination, 50/page.
Pulled all 21,979 headers in ~90 seconds. No FlateDecode, no `requestSubmit()`,
no DOM reading.

```graphql
query($accountId: ID!, $after: String) {
  account(id: $accountId) {
    receipts(first: 50, ledger: COSTS, section: ARCHIVE, after: $after) {
      pageInfo { endCursor hasNextPage }
      totalCount
      edges { node { id date invoiceNumber totalAmount taxAmount netAmount
                     supplier { name } } }
    }
  }
}
```

`receipt(id:)` also exposes `lineItems`, `downloadUrl` (the raw PDF, same-origin,
cookie-auth), and `imageContentType`. Introspection is disabled, but field
probing works and error messages name valid fields.

**This is usable by the existing skill today**, independent of the replacement
project. It would delete a large share of Appendix G, Appendix H and Appendix I.

## 7a. ACCURACY — settled. ILG 03729959, read natively.

I spent hours writing a PDF text parser in JavaScript before noticing Dext
**renders the invoice on screen**. It can just be read — no file transfer, no
FlateDecode, no layout engine. That is what the detail view is for.

Read off the rendered document, ILG inv 03729959 — the invoice Dext reports
**zero** line items for:

| Code | Description | Pack | Qty | Cost | Total | FRT | LUC | TOT |
|---|---|---|---|---|---|---|---|---|
| 175-0420 | ANTICA FORMULA | 6x1LT | 0/1 | 339.58W | 58.01 | REPACK | 58.43 | 64.27 |
| 395-6785P | APEROL | 6x700ML | 1 | 156.94 | 156.94 | 1.69 | 26.44 | 174.49 |
| 305-1949P | BUFFALO TRACE BOURBON 40% | 6x700ML | 0/1 | 282.81 | 48.32 | REPACK | 48.74 | 53.61 |
| 360-1310 | ROOSTER ROJO TEQUILA BLANCO | 6x700ML | 3 | 280.10 | 840.30 | 1.69 | 46.96 | 929.90 |
| 345-5638P | SAILOR JERRY SPICED RUM | 6x700ML | 0/2 | 235.54 | 80.47 | REPACK | 40.66 | 89.44 |
| 122-2867 | ALEHOUSE CRISP KEG | 1xKEG49. | 1 | 160.00 | 160.00 | 8.13 | 168.13 | 184.94 |
| 122-2858 | ALEHOUSE PREMIUM KEG | 1xKEG49. | 2 | 185.00 | 370.00 | 8.13 | 193.13 | 424.88 |
| 115-3762 | CORONA MEXICAN 6PK BRW BX R | 24x355ML | 1 | 54.41 | 54.41 | 1.69 | 14.02 | 61.71 |
| 117-4213 | HEAPS NORMAL QUIET XPA NON ALC | 24x375ML | 1 | 56.56 | 56.56 | 1.69 | 9.71 | 64.08 |
| 460-1504 | COCA COLA | 12x1.25LT | 1 | 38.60 | 38.60 | 1.69 | 3.36 | 44.32 |
| 460-2567 | COCA COLA CAN CUBES | 24x375ML | 1 | 39.54 | 39.54 | 1.69 | 1.72 | 45.36 |
| 460-1639 | COKE NO SUGAR 1.25 LITRE | 12x1.25LT | 1 | 38.60 | 38.60 | 1.69 | 3.36 | 44.32 |
| 450-1293 | S.PELLEGRINO SPARKLING WATER | 24x500ML | 1 | 49.71 | 49.71 | 1.69 | 2.14 | 56.54 |
| 460-3254 | SPRITE 375ML 24 CUBE | 24x375ML | 1 | 39.54 | 39.54 | 1.69 | 1.72 | 45.35 |

**Self-validating:**

```
sum(TOT incl)  = 2283.21   vs stated 2283.19   -> 2 cents, 14 lines
sum(Total ex)  = 2031.00   vs stated 2031.00   -> exact
2031.00 + 35.70 freight + 8.93 fuel = 2075.63 x 1.1 = 2283.19   ✓
GST = 2283.19 / 11 = 207.56   ✓ matches the printed figure
```

14 independently-read figures reconciling to a 15th printed elsewhere on the
document. Locked in as `tests/test_ilg_03729959.py` — 32 tests.

## 7b. NEW HAZARD FOUND — the `LUC` column is a trap

Appendix B calls LUC "Last Unit Cost ex GST" and treats it as per-unit.
**Its unit varies per product and is not derivable from the invoice.**

Implied units = `(Total + FRT x qty) / LUC`:

| Product | Pack | LUC | Implied units |
|---|---|---|---|
| COCA COLA CAN CUBES | `24x375ML` | $1.72 | **24** — per can |
| SPRITE 375ML 24 CUBE | `24x375ML` | $1.72 | **24** — per can |
| HEAPS NORMAL QUIET XPA | `24x375ML` | $9.71 | **6** — per 4-pack |
| CORONA MEXICAN 6PK | `24x355ML` | $14.02 | **4** — per 6-pack |

Heaps Normal and Coca Cola Can Cubes carry an **identical pack string** and have
**different LUC bases** — 6 vs 24, a 4× gap with nothing to tell them apart.
Corona is only inferable because "6PK" is in the name. Heaps isn't inferable at all.

Cost of using LUC as the unit price — silent, 30 days, per skill Rule 8:

```
Heaps Normal    $9.71  vs  $2.67 correct   ->  3.6x high
Corona         $14.02  vs  $2.57 correct   ->  5.5x high
```

Appendix B's `TOT / (qty x pack_size)` rule is correct and lands in-bounds on all
14 lines. Now enforced in config as `luc_column_unit_basis: varies_do_not_use`,
with a regression test guarding it.

**Also corrected:** Appendix B only ever documents repacks as `0/1`, which reads
as "a repack is one bottle". This invoice has `0/2` (Sailor Jerry). Notation is
`0/N`. A test asserting `repack_qty == 1` was wrong and has been fixed.

## 7c. The validator caught its own author

The first version of the real-invoice test used `tot / qty`, forgetting pack
size. `SANITY_BOUNDS` fired on four lines — Corona at $61.71/can against the
$0.80–8.00 bound. That is exactly the case-total-in-a-per-unit-field bug the
check was written for, and it caught it on real data, against the person who
wrote both. Kept in the test history deliberately.

First real keg prices also landed: ALEHOUSE CRISP $184.94, ALEHOUSE PREMIUM
$212.44, Grifter $292.05 — all inside `per_keg` $100–600. Appendix B's $34.65
would have been flagged, correctly.

## 7d. PDF stream decoding — proven, then deliberately abandoned

The liquor PDFs *do* contain everything. Decoded ILG invoice 03729959
(14-JUL-2026, $2,283.19) — an invoice Dext holds **zero** line items for — by
inflating its FlateDecode streams with native `DecompressionStream` (CSP blocks
blob:/Response, so the stream reader must be driven by hand):

```
Account No. 2428 — STOWAWAY FRESHWATER
Code      Description                Pack      Qty   Cost   Total  FRT   LUC    TOT
395-6785P APEROL                     6x700ML         54.41  156.94 1.69  26.44  174.49
305-1949P BUFFALO TRACE BOURBON 40%  6x700ML         48.32  REPACK       48.74
175-0420  ANTICA FORMULA             6x1LT     0/1   ...    58.01  REPACK       58.43
```

Column headers match Appendix B exactly. `REPACK` and the `0/1` qty notation are
both visible, as documented.

I stopped here on purpose. Reconstructing exact column alignment needs a full PDF
text-state machine (`Tm` scale factors, `TD` in unscaled text space, per-glyph
positioning) — I was three layers into reimplementing a layout engine in
JavaScript, inside a browser, to defeat a CSP rule, for a pipeline whose entire
premise is that **Claude reads PDFs natively so nobody has to do this**. That's
a rabbit hole, not a milestone. Production path is: PDF → Claude API → JSON.

The proof stands: the data is in the PDF, it is rich, and Dext has none of it.

---

## 8. THE ACCURACY TEST — extraction 14/14, human receive 13/14

Stowaway PO 54361209 vs ILG invoice 03729959. Three independent sources for one
delivery: the invoice, my extraction, and what a human actually received.
Full record in [data/golden-po-54361209.md](data/golden-po-54361209.md).

```
Invoice stated total: 2283.19
  my extraction:  2283.21   (+0.02)  <- rounding drift, sum-of-rounded-lines
  LS receive:     2283.16   (-0.03)

Exact: 10/14    within 1c: 3/14    >1c apart: 1/14
```

The single >1c divergence is **Lightspeed's, not mine**. Coke 1.25L: the invoice
says `44.32` — verified by zoom, `(38.60 + 1.69) × 1.1 = 44.32` — and Lightspeed
received `44.28`. My +2c is legitimate rounding (the invoice total derives from
the ex-GST subtotal × 1.1, not by summing rounded per-line TOTs).

WOS handled correctly on this one: De Bortoli, Fee Bros and Sprite 1.25L all
received at qty 0 / $0.00 per Rule 5.

## 9. HARD BLOCKER — product names don't match, and fuzzy matching will mis-map

Appendix B: *"Match on brand + product type + size."* **Broken on real data:**

| ILG code | Invoice says | Lightspeed calls it |
|---|---|---|
| 122-2867 | `ALEHOUSE CRISP KEG` | **Alehouse Summer Mid [Keg]** |
| 122-2858 | `ALEHOUSE PREMIUM KEG` | **Alehouse Draught Lager [Keg]** |
| 460-1639 | `COKE NO SUGAR 1.25 LITRE` | **Coke Zero 1.25L** |

"Crisp" vs "Summer Mid". "Premium" vs "Draught Lager". Zero overlap in the
distinguishing words. And this is the nasty part — both ILG lines are
`ALEHOUSE * KEG`, so a fuzzy matcher scores a *partial hit on the generic
tokens* and looks like it worked, while coin-flipping between two different
kegs at **$184.94 vs $212.44**. A wrong pick is silent and plausible.

Worse, **the same code has different names per venue**:

| ILG code | Stowaway | Harry Gatos |
|---|---|---|
| 122-2858 | Alehouse **Draught Lager** [Keg] | Alehouse **Premium Lager** [Keg] |

Appendix A documents venue-specific ProductIDs. Names diverge too — from each
other *and* from the supplier. A global description→product map is impossible.

**Consequence:** resolution must key on the supplier's item code (ILG `122-2858`,
IWI `GHEMILL-24`), via a per-venue code→ProductID table. It cannot be inferred.
Now encoded as `product_resolution.fuzzy_description_matching: forbidden`, with
14 seed mappings and 6 regression tests guarding it.

This was invisible until a real invoice met a real PO.

## What this changes

**Accuracy is no longer an open question.** 14 lines off a real ILG invoice,
reconciling to its own stated total within 2 cents, on a document Dext has
nothing for. Reading them needs no file transfer and no PDF library — Dext
renders the invoice and it can simply be read.

**The parallel run, as designed, is dead.** I proposed A/B-ing my extraction
against Dext's line items. For 79% of spend — and for 100% of liquor — there is
no baseline to compare to.

**But it matters less than I thought.** The self-reconciliation IS the check.
An invoice whose lines sum to its own printed total is right, with or without a
second opinion. Lightspeed remains the better source for calibrating BOUNDS
(real per-unit prices at scale), but it is no longer needed to prove accuracy.

**The bar is much lower than I thought.** An LLM extractor isn't replacing Dext's
extraction. There is nothing to replace. It's filling a hole that has always
been there, currently plugged by a human driving Chrome.

**The COGS goal is worth more than I priced it.** 79% of spend has no structured
line-item data anywhere today. Your COGS mapping has nothing to consume.

**Sanity bounds cannot be calibrated from Dext** — my task #9 is blocked, not
done. Bounds need per-unit prices; Dext has no qty field, and the liquor
suppliers where kegs/bottles/cans live have no line items at all. The bounds in
`suppliers.yaml` remain prose-derived, and that's still the weakest part of the
repo.

**Lightspeed is the real ground truth.** Receive history has qty and unit price
per line, and it's what the skill has been writing for months. Plus
`price-history.csv`. That's the baseline and the bounds source — not Dext.

## Recommended next

1. **Pull Lightspeed receive history** → calibrate bounds, build the golden
   dataset. Needs a Lightspeed session (separate login).
2. **Ship the GraphQL client into the existing skill.** Standalone win, no
   dependency on the rest of this.
3. **Cold-extract a liquor batch** via the production path (PDF → Claude API)
   and measure the real flag rate against Lightspeed receives.

Phase 2 (dropping Dext for Xero) is unaffected and still not recommended yet —
but note the archive is now doing more work than I credited: it's the only place
those 21,979 PDFs live.
