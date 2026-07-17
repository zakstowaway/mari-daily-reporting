# Extraction instructions

**This file IS the extractor.** In production the pipeline is:

```
PDF bytes  →  Claude, with THIS FILE + config/suppliers.yaml in the prompt  →  Invoice JSON  →  validate()
```

There is no fine-tuned model. Nothing is remembered between runs. **If a rule is
not written here or in `suppliers.yaml`, it does not exist.** Every trap below
cost real investigation to find; each one is silent, and each one would be lost
the moment it isn't written down.

Read `config/suppliers.yaml` alongside this. This file is *how to read*;
that file is *what the supplier does*.

---

## 0. The prime directive

**Produce an extraction that reconciles, or refuse.**

You are not trying to be right. You are trying to be *provably consistent*, and
loud when you can't be. `validate()` will reject anything whose lines don't sum
to the invoice's own printed total. That's the point — an invoice that fails is
five minutes of a human's time; one that silently passes wrong is a wrong margin
on a dish for a month.

When unsure of a figure: **zoom in again**. Never read a price off a full-page
view. Getting a digit wrong flows into Average Cost Price → GP → every recipe,
and per skill Rule 8 it persists for 30 days regardless of later corrections.

## 1. Output shape

Emit JSON matching `src/models.py`. Key fields per line:

| Field | Meaning |
|---|---|
| `description` | verbatim from the invoice, don't normalise |
| `supplier_code` | **critical** — the supplier's item code. See §3. |
| `qty` | see §4 — semantics vary by supplier |
| `unit_price_incl` | **GST-INCLUSIVE, per stock unit**. See §2, §5. |
| `line_total_incl` | GST-INCLUSIVE line total |
| `pack_size` | units per case/crate |
| `line_class` | `stock` / `extra` / `wos` — never guess, leave `unknown` |
| `tax_treatment` | `gst` / `gst_free` / `wet` — **per line**, see §2 |
| `cost_basis` | `per_keg` / `per_bottle` / `per_can` / `per_unit` / `per_kg` |
| `raw_qty`, `raw_uom` | the untouched source strings |

## 2. GST — THE most dangerous axis

**Lightspeed cost prices are GST-INCLUSIVE** (skill Rule 2, `CostTaxCode = GST`).
Invoices are often EX-GST. You must convert, and you must know which you're
looking at.

### Read the column header. It tells you.

> **Foodlink SI4467596** — columns literally read `Price Excl. GST` and
> `Amount Excl. GST`. Footer: `Total AUD Excl. GST 264.60` / `GST Amount 4.60`
> / `Total AUD incl. GST 269.20`.
>
> **Dext's own line items for this invoice are the EX-GST figures** and its
> header total is the INCL-GST one. They differ by exactly the GST and can never
> reconcile. Taking Dext's line amounts straight into Lightspeed understates
> every taxable line by 10%.

### Tax is PER LINE, not per invoice

Basic food is GST-free; packaged goods and fees are not. One invoice carries both.

> **Foodlink SI4467596**: barramundi, squid, tortillas, camembert = GST-free.
> Corn chips + Fuel Levy = taxable. `43.00 + 3.00 = 46.00`, GST `$4.60` ✓.
> Four lines where ex == incl, one where it doesn't. **Selective errors are
> harder to spot than total ones.**

Markers to look for: a `GST` column with per-line flags (Foodlink), an asterisk
convention (`(*) INDICATES TAXABLE ITEM` — Select Fresh), a `W` suffix for WET
(ILG's `339.58W`).

### Arithmetic that always holds

- GST component of a GST-inclusive figure = `total / 11` — **also true with WET**,
  since WET applies first and GST is levied on `(ex + WET)`.
- GST can never exceed `total/11`. If it does, you misread something.
- If GST is $0 on a food invoice, that is normal. Do not "fix" it.

## 3. Product identity — code, never description

**`fuzzy_description_matching: forbidden`.** This is not a style preference.

> ILG `122-2867` "ALEHOUSE CRISP KEG" → Lightspeed **"Alehouse Summer Mid [Keg]"** ($184.94)
> ILG `122-2858` "ALEHOUSE PREMIUM KEG" → Lightspeed **"Alehouse Draught Lager [Keg]"** ($212.44)

Both are `ALEHOUSE * KEG`. A fuzzy matcher scores a partial hit on the generic
tokens, looks confident, and coin-flips between two different kegs $27 apart.
"Crisp"/"Summer Mid" and "Premium"/"Draught Lager" share nothing.

The same code has **different names per venue** (`122-2858` is "Draught Lager" at
Stowaway, "Premium Lager" at Harry Gatos). A global name map is impossible.

**Always capture `supplier_code`.** Resolution happens later against
`product_resolution.ilg_codes`. If there's no code and no mapping, emit the line
with `lightspeed_product_id: null` and let it go to review. Do not guess.

## 4. Quantity — semantics vary by supplier

**Never assume `qty` means "number of things".**

> **B&E `CHICKEN BREAST ... 5KG BAG`** — `Ordered 5.00 | Shipped 5.00 | UOM KG |
> Ship Doc 1.00 BAG | $12.20 | $61.00`.
>
> That is **ONE 5kg bag**. The `5.00` is KILOGRAMS, because the line's UOM is KG.
> Read it as a count → 5 bags → 25kg of chicken never delivered. **5× stock,
> 5× COGS, silent.**

- **UOM varies per line.** One B&E invoice: `UNIT`, `KG`, `BAG`, `TUB`.
- **Quantities can be fractional.** Select Fresh `CARROT KG 0.50`.
- **ILG `0/N` = N units as a repack**, not zero. Both `0/1` and `0/2` seen.
  Repack lines show `REPACK` in the FRT column.
- **Ordered ≠ Shipped.** Kitchen suppliers print both columns
  (Select Fresh `Order`/`Supply`, B&E `Ordered`/`Shipped`).
  **Always take the shipped/supplied column.** Short-ships are explicit.

## 5. Unit price — DERIVE IT. NEVER READ A COLUMN.

**Rule, universally: `unit_price_incl = line_total_incl / (qty × pack_size)`.**

Lightspeed stores per **stock unit** and multiplies by pack size itself. Zak
confirmed: Stowaway/HG stock **singles** — no 4-packs or 6-packs.

### THE SINGLE MOST IMPORTANT RULE IN THIS FILE

**Every supplier prints a "unit cost" column. Seven suppliers were checked
against real invoices. NO TWO MEAN THE SAME THING. They are wrong in BOTH
directions, by 17% to 10.9×. The column name tells you nothing.**

| Supplier | Column | What it actually is | vs truth |
|---|---|---|---|
| **ILG** | `LUC ex GST` | **varies PER PRODUCT** — per-can / per-4pk / per-6pk | 3.6–5.5× HIGH |
| **Paramount** | `LUC Ex GST` | per **CASE** | **10.9× HIGH** |
| **Lion** | `UNIT VALUE` | pre-**DISCOUNT** list price | 15–32% HIGH |
| **Combined Wines** | `Unit Price` | pre-disc, pre-WET, pre-GST | **21.7% LOW** |
| **Nelson** | `W/sale Price/Bot` | pre-discount | **17% LOW** |
| **Bacchus** | `LUC` | per btl ex-GST but **incl WET** | ~10% LOW |
| **Viticult** | `LUC (Ex GST)` | per btl post-disc ex-GST | *reliable — still derive* |

**LOW is more dangerous than HIGH.** High inflates cost → GP looks worse →
someone notices. Low deflates cost → **GP looks better → nobody ever notices.**
Combined and Nelson both read low.

The killer case: ILG's Heaps Normal and Coca Cola Can Cubes have an
**identical pack string** (`24x375ML`) and a **4× different LUC basis**
($9.71 per 4-pack vs $1.72 per can). Nothing on the invoice distinguishes them.

Same shape at B&E: the per-KG price is real, but **the Line Total already
reflects it** (`$12.20/KG × 5kg = $61.00` = stated total). Multiplying again
double-counts. **Line Total is authoritative — everywhere.**

## 6. Extras — freight, levies, surcharges. THREE INCOMPATIBLE MODELS.

Anything that isn't a stock item is `line_class: extra`. These are **never**
entered on a Lightspeed receive (standing directive); they're logged to
`price-history.csv` as `delivery_fee_rolled_in`.

**The LS receive total is therefore EXPECTED to be under the invoice total by
exactly `sum(extras)`.** That gap is the green light, not an alarm.

**Freight works three incompatible ways. Read the invoice; never generalise:**

| Model | Suppliers | Behaviour |
|---|---|---|
| **Inside the line total** | **ILG, Lion** | freight is a per-line column/allocation ALREADY in the line total → **nothing to skip; adding it double-counts** |
| **Separate, GST-free** | **Viticult** | footer line; `GST = subtotal × 0.1` excludes it → skip |
| **Separate, GST-taxable** | **Foodlink, Paramount** | real line item(s) with GST → skip |
| Footer at $0.00 | FFT | nothing to do |
| Sometimes absent | Bacchus | a $0.00 gap is legitimate |

> **ILG** shows `Freight 35.70` + `Fuel Levy 8.93` in its summary box, but
> `sum(TOT incl) = $2,283.21 ≈ stated $2,283.19`. The freight is already spread
> across the lines. Adding it **double-counts $44.63**.
>
> **Lion** has `FREIGHT` and `FUEL SURCHARGE` as per-line COLUMNS inside
> `LINE VALUE`. Appendix B says "appears as a separate line" — **wrong**. This is
> why Lion's PO matched its invoice at exactly $0.00.
>
> **Paramount** marks extras with **`Size = MISC`** and gives them product codes
> (9000000 Carton Freight, 10010294 Min Delivery, 9000004 Fuel Levy). They would
> resolve as products if you keyed on code alone. Their sum ($7.15 + $9.35 +
> $0.55) is exactly the **$17.05** PO gap.

**All seven Stowaway PO gaps are now explained** — see
`data/po-invoice-reconciliation.md`. Nothing is left as "worth a look".

## 6b. Wine — the WET formula

Verified 6/6 across **Bacchus, Combined Wines, Nelson**:

```
WET   = net × 0.29
GST   = (net + WET) × 0.10
Gross = net × 1.29 × 1.1  =  net × 1.419
```

Discounts are applied to `net` BEFORE WET. Combined: `165.00 × 0.9 = 148.50` →
WET `43.07` → GST `19.16` → **`210.73`**.

**Viticult has no WET column at all** — `GST = subtotal × 0.10` exactly. Its
prices appear WET-inclusive. Config previously said `wet`; corrected to `gst`,
but this is **unverified** — flag it rather than assume.

## 7. Venue — signals are PER-SUPPLIER

**They do not transfer.** Getting this wrong is silent: stock lands in the wrong
venue, and a cost update against the wrong venue's ProductIDs **does nothing at
all** (Appendix A — IDs are venue-specific).

> **Select Fresh, same day, two invoices:**
>
> | | inv 3084903 | inv 3085647 |
> |---|---|---|
> | Address | `LVL 1, SHP 18, 1-3 MOORE ROAD` | `LVL 1, SHP 18, 1-3 MOORE ROAD` |
> | Delivery code | `182096#` | `182096#` |
> | Delivery instructions | **`BAR AT STOWAWAY`** | — |
> | **Account Code** | **`HARGAT`** | **`STOWA`** |
> | Truth | **Harry Gatos** | Stowaway |
>
> The address is identical on both. `182096#` is on both. The Harry Gatos
> invoice literally says **"BAR AT STOWAWAY"** (HG is upstairs; produce drops at
> the bar). Only `Account Code` discriminates.

`Shop 18/1-3 Moore Rd = Stowaway` and `182096# = Harry Gatos` are **ILG facts**,
not universal ones. Use `venue_resolution.by_supplier`. Decisive when present:
PO number prefix (`54361` = Stowaway, `35985` = Harry Gatos).

**If the venue is unresolved, emit `unknown`.** `validate()` will block it. Never guess.

## 8. Multi-page

B&E `6969915` is `Page 1 of 2`. Check for `Page N of M` and read every page
before emitting. A dropped page fails `INVOICE_RECONCILE` — which is the system
working, but wastes a review cycle.

## 9. Self-check before emitting

1. `sum(line_total_incl)` ≈ stated total (within $0.50)? If not, **you missed a
   line or misread a figure**. Re-read; don't emit.
2. `qty × unit_price × pack_size` = line total, per line?
3. GST ≤ `total/11`?
4. Every unit price plausible? (keg $100–600, spirit bottle $5–400, can $0.80–8)
   An implausible price usually means a case total in a per-unit field.
5. Every line classified? Every line with a `supplier_code`?
6. Venue resolved from a **supplier-specific** signal?

## 10. Worked reference

`tests/test_ilg_03729959.py` — a complete real invoice, 14 lines, reconciling to
$2,283.19 within 2c. `tests/test_kitchen.py` — Select Fresh and B&E.
`data/golden-po-54361209.md` — the same invoice vs what a human actually received
(extraction 14/14, human 13/14).

## Appendix — every trap found, by supplier

One real invoice read from every supplier active in the last 30 days.

| Supplier | Trap | Cost if missed |
|---|---|---|
| **ILG** | `LUC` unit basis **varies per product** | Heaps 3.6× high, Corona 5.5× high |
| ILG | Freight already inside `TOT incl GST` | double-count $44.63 |
| ILG | `0/N` repacks (`0/2` exists, not just `0/1`) | wrong qty |
| ILG | Names unmatchable; two `ALEHOUSE * KEG` | wrong keg, $27/unit |
| **Lion** | Undocumented per-line **DISCOUNT** column (27%) | kegs **15–32% high** |
| Lion | Freight/fuel are per-line COLUMNS in `LINE VALUE` | double-count |
| Lion | Kegs are **49.5L not 50L** | ~1% GP on every schooner |
| **Paramount** | `LUC` is **per CASE** | Sprite **10.9× high** |
| Paramount | `Size = MISC` marks extras, which carry product codes | extras resolve as products |
| Paramount | Uses ILG's `0/N` repack notation | wrong qty |
| **Combined** | `Unit Price` is pre-disc/pre-WET/pre-GST | **21.7% LOW** — GP looks better |
| **Nelson** | `W/sale Price/Bot` is pre-discount | **17% LOW** |
| Nelson | `L.U.C.` is per-btl ex-GST but **incl WET** | ~10% low |
| **Bacchus** | `LUC` per-btl ex-GST incl WET | ~10% low |
| Bacchus | Fuel levy **sometimes absent** | $0.00 gap is legitimate |
| **Viticult** | **No WET column** despite being wine | config said `wet` — unverified |
| Viticult | Freight is a footer line, **GST-free** | — |
| **Foodlink** | **Line items EX-GST, header INCL-GST** | **taxable lines 10% low** |
| Foodlink | Mixed tax — only 2 of 6 lines taxable | selective, hard to spot |
| Foodlink | **Dext records $0.00 GST** on invoices stating GST | see `DEXT-GST-ISSUE.md` |
| **B&E** | `qty` is in UOM units; KG lines are **weight** | **5× stock** |
| B&E | UOM varies per line (UNIT/KG/BAG/TUB) | — |
| B&E | Line Total already includes the KG multiply | double-count |
| B&E | Multi-page | dropped lines |
| **Select Fresh** | Address + `182096#` **identical across venues** | wrong venue, silent |
| Select Fresh | HG invoice reads "BAR AT STOWAWAY" | resolves **backwards** |
| Select Fresh | Fractional qty (`0.50` KG) | — |
| **Gulli** | Amounts ex-GST; explicit `0%`/`10%` rate column | taxable lines 10% low |
| Gulli | `Standard Delivery` at **$0.00** is still an extra | resolves as a product |
| **Sun Circle** | **HANDWRITTEN**; Dext "unable to fully extract" | $3,168/mo invisible |
| **Jun Pacific** | Tax letter codes `'G'`/`'W'` | — |
| Grifter | Appendix B's `$34.65/keg` is fiction; real $292.05 | — |
| *(pipeline)* | Unanchored extras regex swallowed a real keg | **stock silently deleted** |

### Tax marking — five conventions

`Gulli` rate column (`0%`/`10%`) · `Foodlink` flag column · `Select Fresh`
asterisk footnote · `Jun Pacific` letter codes (`G`/`W`) · `Paramount` per-line
WET+GST amounts. **All print amounts ex-GST.**

### Still UNVALIDATED — do not assume

- **Andrews Meat catch-weight.** Billing actual kilos against a counted order
  would make `qty × unit = total` fail on *correct* invoices → false positives,
  which train people to ignore alarms. **LOW priority**: $571 in 30 days, down
  from $338k lifetime. M&J Chickens is dead (147 days).
- **Viticult WET** — no WET column seen; config guessed `gst`.
- **Foodlink stock units** — `CTN-6` / `CTN-12`; carton vs single unknown.
- **Multi-page** — B&E and Jun Pacific are `Page 1 of 2`; page 2 never read.
- **Paramount substitutions** — documented in Appendix B, not seen this sweep.
