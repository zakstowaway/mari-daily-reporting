/**
 * Dext GraphQL client.
 *
 * Discovered 16 Jul 2026. Dext's own UI is a GraphQL client; we can just use
 * the same endpoint. Cookie-authenticated, so this runs IN THE BROWSER with a
 * logged-in session (paste into the Chrome JS tool).
 *
 * This replaces essentially all Dext scraping in dext-lightspeed-invoices:
 *   - no archive DOM reading
 *   - no form.requestSubmit() search hack
 *   - no dext_archive_links.js
 *   - no per-invoice page navigation
 *
 * Pulled all 21,979 archive headers in ~90 seconds.
 *
 * ── IMPORTANT CAVEAT ───────────────────────────────────────────────────────
 * `lineItems` is NOT a reliable source. Measured across the archive:
 *   - Every major liquor supplier returns ZERO line items (ILG: 0/40 over
 *     five years, $821k of spend).
 *   - `LineItem` has NO quantity field at all — it is an accounting split for
 *     Xero, not a procurement line. You cannot get qty x unit price from it.
 *   - Coverage CHANGES SILENTLY. B&E Foods returned nothing until 2026-05-12,
 *     then started working, and still drops out intermittently (19/40).
 * Treat lineItems as an opportunistic bonus. The PDF (`downloadUrl`) is the
 * real source. See FINDINGS.md.
 * ───────────────────────────────────────────────────────────────────────────
 */

const DEXT = {
  endpoint: '/graph/api',

  // Base64 of "Account-2025600". Stable for this tenant.
  // Re-derive by watching any CostsQuery if it ever changes.
  accountId: 'QWNjb3VudC0yMDI1NjAw',

  // App path moved gamma -> delta at some point before Jul 2026.
  // The skill's documented /gamma/ URLs redirect but are stale.
  urls: {
    archive: 'https://app.dext.com/delta/costs/archive',
    inbox:   'https://app.dext.com/delta/costs/inbox',
    detail:  (id) => `https://app.dext.com/delta/costs/archive/${id}/details`,
  },

  async gql(query, variables = {}) {
    const r = await fetch(this.endpoint, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, variables }),
    });
    const j = await r.json();
    if (j.errors) throw new Error('GraphQL: ' + j.errors.map(e => e.message).join(' | '));
    return j.data;
  },

  /**
   * Page through a section. `first` caps at 50 server-side regardless of what
   * you ask for. ~90s for the full 21,979-row archive.
   *
   * NOTE: a single call can exceed the 45s CDP timeout. Kick it off
   * fire-and-forget and poll — it keeps running after the tool call returns:
   *     window.__p = DEXT.listReceipts({onPage: (a)=>window.__n = a.length});
   */
  async listReceipts({ section = 'ARCHIVE', ledger = 'COSTS', maxPages = Infinity,
                       onPage = null } = {}) {
    const Q = `query L($accountId: ID!, $after: String){
      account(id:$accountId){
        receipts(first:50, ledger:${ledger}, section:$section, after:$after){
          pageInfo{ endCursor hasNextPage }
          totalCount
          edges{ node{
            id date invoiceNumber code totalAmount taxAmount netAmount currencyCode
            supplier { name }
          } }
        }
      }
    }`.replace('$section', `${section}`);
    const out = [];
    let after = null, pages = 0;
    while (pages < maxPages) {
      const d = await this.gql(Q, { accountId: this.accountId, after });
      const c = d.account.receipts;
      c.edges.forEach(e => out.push(e.node));
      pages++;
      if (onPage) onPage(out, c.totalCount);
      if (!c.pageInfo.hasNextPage) break;
      after = c.pageInfo.endCursor;
    }
    return out;
  },

  /** Full detail for one receipt, including lineItems and the PDF URL. */
  async receipt(id) {
    const Q = `query R($id: ID!){
      receipt(id:$id){
        id date invoiceNumber code totalAmount taxAmount netAmount currencyCode
        supplier { name }
        downloadUrl imageContentType documentFileSize
        areLineItemsAllowed lineItemsExtractionIncomplete
        lineItems { id description totalAmount netAmount taxAmount
                    unitNetAmount unitTotalAmount }
      }
    }`;
    return (await this.gql(Q, { id })).receipt;
  },

  /**
   * Batched line-item fetch via aliases. 25/request is comfortable.
   * Returns { receiptId: [lineItem, ...] }.
   */
  async lineItemsFor(ids, chunk = 25) {
    const out = {};
    for (let i = 0; i < ids.length; i += chunk) {
      const c = ids.slice(i, i + chunk);
      const sel = c.map((id, k) =>
        `r${k}: receipt(id:"${id}"){ id lineItems { id description totalAmount
           netAmount taxAmount unitNetAmount unitTotalAmount } }`).join('\n');
      const d = await this.gql(`query{ ${sel} }`);
      Object.values(d).forEach(n => { if (n) out[n.id] = n.lineItems; });
    }
    return out;
  },

  /** Raw PDF bytes. Same-origin, cookie-auth. This is the real data source. */
  async pdf(downloadUrl) {
    const r = await fetch(downloadUrl, { credentials: 'include' });
    if (!r.ok) throw new Error('PDF fetch ' + r.status);
    return new Uint8Array(await r.arrayBuffer());
  },
};

/**
 * Field discovery, for when the schema shifts.
 *
 * Introspection is DISABLED (`__type` / `__schema` both rejected), but the
 * server names valid fields in its errors, so probing works:
 *
 *   await DEXT.gql(`query($id:ID!){ receipt(id:$id){ nonsense } }`, {id})
 *   -> "Field 'nonsense' doesn't exist on type 'Receipt'"
 *
 *   await DEXT.gql(`query($id:ID!){ receipt(id:$id){ category } }`, {id})
 *   -> "field 'category' returns Category but has no selections.
 *       Did you mean 'category { ... }'?"
 *
 * Known LineItem fields (COMPLETE as of 2026-07-16):
 *   id, description, totalAmount, netAmount, taxAmount,
 *   unitNetAmount, unitTotalAmount, baseTotalAmount, category { ... }
 * Confirmed ABSENT: quantity, unitPrice, code, sku, productCode, taxRate,
 *                   discount, uom, packSize
 */

if (typeof window !== 'undefined') window.DEXT = DEXT;
