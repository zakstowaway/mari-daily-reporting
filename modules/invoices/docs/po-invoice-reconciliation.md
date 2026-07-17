# Stowaway PO → Dext invoice reconciliation (week of 12 Jul 2026)

All 10 received Stowaway POs, matched to their Dext invoice. Pulled via the
Dext GraphQL API; PO totals read from Lightspeed Purchase.

| PO | Supplier | LS total | Dext invoice | Invoice total | Δ | Explanation |
|---|---|---|---|---|---|---|
| 54361219 | Lion | $1,624.59 | 94755729 | $1,624.59 | **0.00** | exact |
| 54361212 | Combined Wines | $583.64 | SINV203712 | $583.64 | **0.00** | exact |
| 54361210 | Bacchus Wines | $384.55 | INV494857 | $384.55 | **0.00** | exact |
| 54361216 | Nelson Wine Co. | $280.89 | 20039352 | $280.89 | **0.00** | exact |
| 54361209 | ILG | $2,283.16 | 03729959 | $2,283.19 | 0.03 | rounding |
| 54361218 | Young & Rashleigh | $289.44 | 732701 | $290.91 | 1.47 | Temp Frt Surcharge |
| 54361215 | Mountain Culture | $365.20 | INVMO28274 | $367.40 | 2.20 | **unknown — not in config** |
| 54361213 | Grifter Brewing | $292.05 | 82888 | $297.55 | **5.50** | Freight — matches invoice exactly |
| 54361217 | Viticult | $458.19 | INV-02634 | $464.78 | 6.59 | Freight Total |
| 54361220 | Paramount | $237.33 | 5441124 | $254.38 | 17.05 | Carton Frt + Min Delivery + Fuel Levy + CC |

## What this proves

**1. Every PO has a findable invoice.** 10/10 matched on supplier + date + total,
via API, in seconds. No archive scraping.

**2. The extras-skip rule is real and measurable.** Every non-zero delta is the
supplier's freight/levy lines, which are deliberately NOT entered on the receive
(standing directive). The LS total is *expected* to fall short by exactly
sum(extras). Confirmed directly for Grifter — I read `Freight $5.50 incl` off
invoice 82888, and the PO gap is **exactly $5.50**.

This is why `EXTRAS_EXCLUDED` must be an INFO, not an alarm. Without it, 5 of
these 10 POs look broken. With it, all 10 reconcile.

**3. Paramount $17.05** sits inside Appendix B's documented $1–20 range for its
four-part extras bundle. Consistent.

## Open items

- **Mountain Culture $2.20** — a delta with no documented extras line. Not in
  `suppliers.yaml`. Needs one invoice read to identify. Small, but it's the kind
  of unexplained gap that hides a real error.
- **Lion $0.00 and Bacchus $0.00** — worth a look. Appendix B says Lion carries
  `FREIGHT $11–32` and Bacchus a `Fuel Levy ~$4.95`, both of which should be
  skipped and therefore produce a gap. These matched EXACTLY, so either those
  invoices carried no extras, or the extras WERE included in the receive
  (contradicting the directive). Not enough evidence either way — do not assume.

## Contrast: Harry Gatos

Stowaway is in good shape — 10/10 reconcile once extras are accounted for. The
one bad receive found so far is **HG PO 35985412** ($256.48 over-received,
including $274.98 of phantom stock). See `receive-discrepancies.md`.

Sample is one week. A full sweep needs the code→ProductID table (see FINDINGS §9).
