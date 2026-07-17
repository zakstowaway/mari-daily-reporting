# ⚠ Dext records $0.00 GST on Foodlink invoices that state a GST amount

**For Zak → Donna (bookkeeper). Not an automated fix. Not accounting advice.**

Found 16 Jul 2026 while testing whether Dext's structured data could replace
reading the PDF. It can't — and this is why.

## The facts

Two Foodlink invoices read directly off the document:

| Invoice | Date | Total (incl) | Invoice states GST | **Dext records** |
|---|---|---|---|---|
| SI4467596 | 16 Jul 2026 | $269.20 | **$4.60** | **$0.00** |
| SI4461607 | 13 Jul 2026 | $167.20 | **$0.30** | **$0.00** |

**SI4467596** footer, verbatim:
```
Total AUD Excl. GST   264.60
GST Amount              4.60
Total AUD incl. GST   269.20
```
Taxable lines: `CORN CHIPS 43.00` + `Fuel Levy 3.00` = `46.00` × 10% = **$4.60** ✓

**SI4461607** footer, verbatim:
```
Fuel Levy                3.00
Total AUD Excl. GST    166.90
GST Amount               0.30
Total AUD Incl. GST    167.20
```
Taxable: the `Fuel Levy 3.00` × 10% = **$0.30** ✓

## Dext's API for SI4467596

```
headerTotal: 269.20        <- correct (incl GST)
headerTax:     0.00        <- invoice says 4.60
lineItems (all six):
  BARRAMUNDI ...  total 83.00  net 83.00  tax 0.00   cat "115 - Purchases - Food"
  CORN CHIPS  ...  total 43.00  net 43.00  tax 0.00   cat "115 - Purchases - Food"  <- TAXABLE
  Fuel Levy        total  3.00  net  3.00  tax 0.00   cat "115 - Purchases - Food"  <- TAXABLE, and not food
```

Three problems in one record:
1. **Header tax is $0.00** when the invoice states $4.60.
2. **Every line's tax is $0.00**, so there's no way to tell which lines are taxable.
3. **Line items are EX-GST while the header is INCL-GST** — different bases, so
   they never reconcile. The $4.60 "missing" is simply the GST.
4. **Fuel Levy categorised as "Purchases - Food."**

## Scale — UNQUANTIFIED, needs checking

- **52 of 52** sampled Foodlink invoices show `taxAmount: 0.00` in Dext. No exceptions.
- Foodlink lifetime: **2,092 invoices, $819,412** — the largest kitchen supplier.
- Every Foodlink invoice seen carries a **$3.00 Fuel Levy**, which is taxable
  (= $0.30 GST minimum per invoice), plus GST on any packaged goods.
- These invoices are **published to Xero** (both show "Published to Xero").

**I have verified TWO invoices. Everything beyond that is extrapolation from
n=2, and I am not an accountant.** The two data points differ wildly as a share
of total (1.7% and 0.18%), so do not scale them naively.

## What to ask Donna

1. Does what landed in **Xero** match the GST printed on the Foodlink invoices,
   or did the $0.00 carry through? It may be corrected downstream — I can't see
   Xero from here.
2. If it carried through: are Foodlink GST input credits being claimed on the BAS?
3. Is this Foodlink-specific, or does it affect other mixed-tax suppliers?
   **Gulli** is the one to check next — `CI-424608`, $352.26 total, Dext records
   **$5.84** tax there (non-zero!), so its behaviour differs. Gulli is $298,130
   of spend.

## Why this matters to the pipeline

This was the test of whether Dext's structured data could remove the model call
from the extractor. **Answer: no.** Not because the format is awkward — because
the data is wrong. A deterministic parser built on Dext inherits:

- ex-GST line amounts silently treated as incl-GST → **taxable lines 10% low**
- zero tax flags → **cannot determine per-line taxability at all**
- a Fuel Levy classified as food

The document is the only trustworthy source. That is what the Claude API call in
the pipeline is buying, and it's why `validator.py` re-derives GST from
`total/11` rather than trusting any reported tax field.

## Guard in place

`tests/test_kitchen.py::test_dext_lineitems_do_NOT_reconcile_to_dext_header_THE_TRAP`
and `::test_using_dext_amounts_directly_understates_taxable_lines_by_10pc`
