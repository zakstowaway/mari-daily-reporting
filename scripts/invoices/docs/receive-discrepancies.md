# Receive discrepancies found by PO-vs-invoice comparison

Live errors in Lightspeed, found by comparing received POs against the actual
Dext invoice for the same delivery. Not hypotheticals.

---

## HG · PO 35985412 · ILG · delivered 04 Jul 2026 — $256.48 over-received

**Invoice 03721575** (ILG, 30 Jun 2026, account 3622 = Harry Gatos), total **$203.50**:

| Code | Description | Pack | Qty | Cost | Total | FRT | LUC | TOT |
|---|---|---|---|---|---|---|---|---|
| 122-2858 | ALEHOUSE PREMIUM KEG | 1xKEG49. | 1 | 185.00 | 185.00 | 0.00 | 185.00 | **203.50** |
| 360-1310 | ROOSTER ROJO TEQUILA BLANCO | 6x700ML | **WOS** | 280.10 | .00 | 0.00 | | **0.00** |
| | *Unavailable from Supplier until Unknown* | | | | | | | |

**Lightspeed PO 35985412 received:**

| Product | Qty | Price | |
|---|---|---|---|
| Alehouse Premium Lager [Keg] | 1 | $185.00 | should be **$203.50** — ex-GST used as incl |
| Rooster Blanco Tequila [Bottle] (Crates of 6) | 1 | $274.98 | **WOS — NEVER DELIVERED** |
| | | **$459.98** | invoice says **$203.50** |

### Two errors

1. **Ex-GST price received as GST-inclusive.** $185.00 vs $203.50 — exactly the
   $18.50 GST component. Understates the keg 9.1%, feeds Average Cost Price,
   overstates GP on every schooner of Alehouse Premium for 30 days (Rule 8).

2. **PHANTOM STOCK — $274.98.** Rooster Rojo was flagged `WOS` on the invoice and
   billed at $0.00. ILG never shipped it. Lightspeed thinks Harry Gatos holds a
   crate of 6 tequila bottles that does not exist.

### Rules broken

- **Rule 5** — "WOS items. Receive qty = 0, leave price as-is, flag to Zak."
- **Rule 7 gate** — "sum of PO line totals should match Dext invoice total within
  $0.50. If it doesn't, STOP and investigate — don't receive." Gap: **$256.48**.
- **Rule 2** — "Cost prices in Lightspeed are GST-inclusive."

### What would have caught it

`INVOICE_RECONCILE` — hard ERROR, blocks the write. sum(lines)=$459.98 vs
stated $203.50. No judgement call, just arithmetic.

### Remediation (skill Rule 7)

Receives are read-only after submission. Fixing this needs delete + recreate at
the correct prices — and **DO NOT EMAIL THE SUPPLIER** on the recreate (the
stock already arrived; a duplicate order ships it again). BO cost updates alone
will NOT fix it: Average Cost Price is computed from receive transactions, so
the bad number persists for 30 days regardless (Rule 8).

Zak's call, not an automated fix.

---

## Correctly received (control group)

| PO | Supplier | LS total | Dext invoice | Invoice total | Match |
|---|---|---|---|---|---|
| 35985417 | ILG | $341.21 | 03729960 (14 Jul) | $341.20 | ✅ 1c |
| 35985418 | Coopers | $379.93 | 03729961 (14 Jul) | $379.93 | ✅ exact |

Both reconcile. So the process works when followed — 35985412 is a miss, not a
systemic collapse. Note Coopers bills through ILG, confirming that alias.

Also confirms the cross-check: **Alehouse Premium Lager [Keg] = $212.44** on PO
35985417 exactly matches the $212.44/keg derived independently from Stowaway
invoice 03729959 ($424.88 ÷ 2). Two sources, two methods, same number.
