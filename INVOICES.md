# Supplier invoices → COGS

Reads supplier invoices, refuses to be silently wrong, files the result.
Lives in `scripts/invoices/`. Entry point `scripts/invoice_run.py`.

**Status: the knowledge is done and tested (183 tests). The wiring is not live.**
See *Not done yet* at the bottom — the honest list.

## Pipeline

```
supplier invoice email
  → Outlook rule → Pipedream            (same bridge as PIPEDREAM_BRIDGE.md)
  → repository_dispatch: invoice-arrived  { pdf_base64, source_filename }
  → .github/workflows/invoice_pull.yml
  → scripts/invoice_run.py
       extract.py   PDF → Claude API → JSON     ← the ONLY model call
       validator.py arithmetic gate             ← no model, ever
  → data/invoices/         PASS
    data/invoices_review/  REVIEW
```

Reuses the Pipedream bridge already proven for Insights CSVs — different event
type, `pdf_base64` instead of `csv_base64`. No Graph app registration, no PAT,
fully headless.

## Why there's a model in the loop

Because **there is no trustworthy structured source**. This was tested, not assumed:

- **Dext holds no line items for any liquor supplier.** ILG — the biggest
  supplier in the business, $821k — is **0/40 across five years**. Bacchus, Lion,
  Paramount, Combined, Philter, Viticult, Nelson: all zero. 79% of spend.
- **Dext's `LineItem` has no quantity field at all.** It's an accounting split
  for Xero, not a procurement line. qty × unit price is not obtainable for anyone.
- **Where Dext does extract, it's wrong.** Foodlink: records **$0.00 GST** on
  invoices that print $4.60 on their face, 52/52 sampled. See `docs/DEXT-GST-ISSUE.md`.
- **Sun Circle's invoices are handwritten.** Dext: *"unable to fully extract."*
  $3,168/month.

A deterministic parser built on Dext inherits all of that silently. The document
is the only source.

**The model never decides.** `extract.py` proposes; `validator.py` — pure
arithmetic — disposes. That gate caught a bug in its own author's test
(a case total in a per-unit field, 4 lines).

## The one rule that matters

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

Wrong in **both directions**, 17% to 10.9×. LOW is the dangerous half — it makes
GP look *better*, so nobody investigates.

```
unit_cost_incl = line_total_incl / (qty × pack_size)      # derive, never read
```

## Files

    scripts/invoice_run.py          entry point
    scripts/invoices/
      EXTRACTION.md                 THE EXTRACTOR — this is the prompt
      suppliers.yaml                supplier rules, 15 verified + evidence
      models.py                     Invoice/InvoiceLine (Decimal, never float)
      validator.py                  the gate
      extract.py                    PDF → Invoice via Anthropic API
      dext_client.js                Dext GraphQL — BACKFILL ONLY, needs a session
      tests/                        183 tests
      docs/                         evidence for every rule
    data/cogs_list.csv              74 products, every one traced to an invoice
    .github/workflows/invoice_pull.yml

`EXTRACTION.md` + `suppliers.yaml` go into the prompt on every call. **There is
no fine-tuned model and nothing is remembered between runs — if a rule isn't in
those two files, it does not exist.**

## Run it

    python3 scripts/invoice_run.py --pdf invoice.pdf
    python3 scripts/invoice_run.py --json extraction.json --dry-run   # no API call
    python3 -m pytest scripts/invoices/tests -q

Exit: `0` PASS · `2` REVIEW · `1` extraction failed.

**REVIEW is not failure.** A flagged invoice costs five minutes. One that
silently passes wrong costs a wrong margin on a dish for ~30 days — Average Cost
Price is computed from receive transactions, so a bad number persists regardless
of later corrections.

## Coverage — 15 suppliers, all reconciled to the cent

B&E $16,611/30d · ILG $11,564 · Lion $6,812 · Foodlink $5,362 · Gulli $5,285 ·
Paramount $3,368 · Sun Circle $3,168 · Bacchus $3,123 · Select Fresh $2,822 ·
FFT $2,359 · Nelson $2,146 · Viticult $2,131 · Grifter $1,872 · Combined $1,853 ·
Jun Pacific $1,644

Dead, do not chase: M&J (147 days), Torino ($507k lifetime, last 2024-07),
Winestock, Gateway, Farmer Joe's. Andrews Meat is alive but collapsed —
$571/30d vs $338k lifetime.

Full detail: `docs/SWEEP-COMPLETE.md`.

## NOT DONE YET — read this before trusting it

- **Nothing is wired.** No Pipedream workflow, no Outlook rule, no
  `ANTHROPIC_API_KEY` secret. The workflow file exists and has never run.
- **`extract.py` has never made a real API call.** Its parsing is tested; the
  round-trip isn't.
- **No `supplier_code` → Lightspeed ProductID table.** This is the hard blocker
  for automated receiving. Product names do NOT match and fuzzy matching is
  worse than useless: ILG `122-2867 "ALEHOUSE CRISP KEG"` is Lightspeed's
  *"Alehouse Summer Mid [Keg]"* ($184.94) and `122-2858 "ALEHOUSE PREMIUM KEG"`
  is *"Alehouse Draught Lager [Keg]"* ($212.44). Both are `ALEHOUSE * KEG`; a
  matcher coin-flips between two kegs $27 apart. Needs a BO export per venue.
- **Nothing writes to Lightspeed or COGS.** `data/cogs_list.csv` is a
  point-in-time snapshot, hand-assembled from the sweep.
- **Unverified:** Viticult WET (no WET column seen; config guesses `gst`),
  Foodlink stock units (`CTN-6`/`CTN-12` — carton vs single unknown), page 2 of
  B&E and Jun Pacific, Andrews catch-weight.

## Two things needing a human, not code

- **HG PO 35985412** — $274.98 of Rooster Rojo received that ILG never delivered
  (`WOS` on invoice 03721575, billed $0.00), plus a keg received at the ex-GST
  price. PO $459.98 vs invoice $203.50. Rule 7 delete+recreate — and **do not
  email the supplier** on the recreate. `docs/receive-discrepancies.md`.
- **Foodlink GST in Xero** — Donna. `docs/DEXT-GST-ISSUE.md`.
