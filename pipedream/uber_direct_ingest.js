/**
 * Pipedream step — Uber Direct daily invoice -> data/uber_direct_daily.csv
 * =========================================================================
 * Marilyna's own online orders delivered by Uber's fleet are billed DAILY:
 * one invoice email per day, total = the delivery fee Mari pays Uber that day.
 * Uber already emails these to the Pipedream trigger address
 * (emk22u1j4tx8e1f@upload.pipedream.net — set as a statement recipient in the
 * Uber Direct Billing page). This step parses each email and upserts the day's
 * fee into the reporting repo, where the dashboard reads it.
 *
 * SETUP (once):
 *   1. Open the workflow that has the Email trigger (emk22u1j4tx8e1f).
 *   2. Add a new step -> "Run custom code" (Node.js). Paste everything below.
 *   3. Add a Pipedream Environment Variable named GITHUB_TOKEN:
 *        - a GitHub fine-grained PAT
 *        - Repository access: zakstowaway/mari-daily-reporting
 *        - Permission: Contents -> Read and write
 *      (You create/paste this in Pipedream; it is never shared here.)
 *   4. Deploy.
 *   5. Forward one recent Uber Direct invoice email to the trigger address,
 *      open the step's results, and check the `parsed` export shows the right
 *      { date, shop, fee }. If the amount or date is off, send me the email
 *      body and I'll tighten the two parsing lines flagged below.
 *
 * WHY max($) for the fee: a daily Direct invoice states a single charge total;
 * it is the largest A$ figure in the email. WHY billing-period start for the
 * date: "17 July 2026 - 18 July 2026" means the 17 Jul trading day. Both are
 * marked VERIFY — confirm against a real email before trusting unattended.
 */
import { Buffer } from "buffer";

const REPO   = "zakstowaway/mari-daily-reporting";
const BRANCH = "main";
const FILE   = "data/uber_direct_daily.csv";
const HEADER = "date,shop,fee_inc_gst,source";
const SHOP   = "mari";
const SOURCE = "uber_direct_email";

export default defineComponent({
  async run({ steps, $ }) {
    const token = process.env.GITHUB_TOKEN;
    if (!token) {
      throw new Error(
        "Add a Pipedream env var GITHUB_TOKEN (fine-grained PAT, Contents: Read+Write on " + REPO + ")."
      );
    }

    const ev = steps.trigger.event || {};

    // ---- 1) Parse the invoice email -----------------------------------
    const subject = ev.subject || "";
    const text =
      ev.text || (ev.body && ev.body.text) || htmlToText(ev.html || "");
    const hay = `${subject}\n${text}`;

    // VERIFY (amount): daily charge total, e.g. "A$15.23".
    const money = [...hay.matchAll(/A?\$\s*([0-9][0-9,]*\.[0-9]{2})/g)].map((m) =>
      parseFloat(m[1].replace(/,/g, ""))
    );
    if (!money.length) {
      throw new Error(
        "No A$ amount found — check the parser against this email:\n" +
          hay.slice(0, 800)
      );
    }
    const fee = Math.max(...money);

    // VERIFY (date): start of the billing period, else email date - 1 day.
    const date =
      billingStart(hay) ||
      prevDay(ev.headers?.date || ev.date || new Date().toISOString());

    const row = {
      date,
      shop: SHOP,
      fee_inc_gst: fee.toFixed(2),
      source: SOURCE,
    };
    $.export("parsed", row); // <-- eyeball this on the first real run

    // ---- 2) Upsert the row into the CSV on GitHub ---------------------
    const api = `https://api.github.com/repos/${REPO}/contents/${encodeURIComponent(
      FILE
    ).replace(/%2F/g, "/")}`;
    const gh = {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "pd-uber-direct",
    };

    // Contents API can 409 if another commit lands between GET and PUT; retry.
    for (let attempt = 0; attempt < 4; attempt++) {
      let sha = null;
      let lines = [HEADER];

      const get = await fetch(`${api}?ref=${BRANCH}`, { headers: gh });
      if (get.status === 200) {
        const j = await get.json();
        sha = j.sha;
        const cur = Buffer.from(j.content, "base64").toString("utf8").trim();
        lines = cur ? cur.split(/\r?\n/) : [HEADER];
        if (lines[0] !== HEADER) lines.unshift(HEADER);
      } else if (get.status !== 404) {
        throw new Error(`GitHub GET failed ${get.status}: ${await get.text()}`);
      }

      // upsert by (date, shop): drop any existing row for this key, add ours
      const key = `${row.date},${row.shop},`;
      const bodyRows = lines
        .slice(1)
        .filter((l) => l.trim() && !l.startsWith(key));
      bodyRows.push(`${row.date},${row.shop},${row.fee_inc_gst},${row.source}`);
      bodyRows.sort();
      const out = [HEADER, ...bodyRows].join("\n") + "\n";

      const put = await fetch(api, {
        method: "PUT",
        headers: gh,
        body: JSON.stringify({
          message: `Uber Direct fee ${row.date} ${row.shop} A$${row.fee_inc_gst} (email)`,
          content: Buffer.from(out, "utf8").toString("base64"),
          sha: sha || undefined,
          branch: BRANCH,
        }),
      });

      if (put.ok) return { committed: row, file: FILE, attempt: attempt + 1 };
      if (put.status === 409) continue; // sha race — refetch and retry
      throw new Error(`GitHub PUT failed ${put.status}: ${await put.text()}`);
    }
    throw new Error("GitHub PUT kept conflicting (409) after retries.");
  },
});

function htmlToText(h) {
  return String(h)
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

function billingStart(s) {
  const M = {
    january: "01", february: "02", march: "03", april: "04",
    may: "05", june: "06", july: "07", august: "08",
    september: "09", october: "10", november: "11", december: "12",
  };
  const m = s.match(/([0-9]{1,2})\s+([A-Za-z]+)\s+([0-9]{4})/);
  if (!m) return null;
  const mo = M[m[2].toLowerCase()];
  if (!mo) return null;
  return `${m[3]}-${mo}-${String(m[1]).padStart(2, "0")}`;
}

function prevDay(iso) {
  const d = new Date(iso);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}
