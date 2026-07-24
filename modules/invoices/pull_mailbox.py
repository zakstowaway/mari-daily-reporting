#!/usr/bin/env python3
"""
Capture supplier invoices from the accounts@stowawaybar.com mailbox — the
Dext-free ingestion step.

    python3 modules/invoices/pull_mailbox.py            # process new invoices
    python3 modules/invoices/pull_mailbox.py --dry-run  # list only, no changes

FLOW
  Graph (accounts@ inbox, token auth)  ->  every message with a PDF
    ->  run.py (extract via ANTHROPIC_API_KEY, then validate)
    ->  PASS   -> data/invoices/         + move email to "Invoices Processed"
    ->  REVIEW -> data/invoices_review/  + move email to "Invoices Review"
    ->  build_cogs_list + build_costs    ->  git commit

WHY A DEDICATED INBOX, NOT A MAIL RULE
  Suppliers send to accounts@. We process EVERY PDF and let the validator decide
  what's a real invoice (it must reconcile to the printed total). No sender
  matching to rot silently; anything that isn't a clean invoice lands in the
  visible Review folder. Moving each message out of the inbox is the "done"
  marker — idempotent, and you can see exactly what needs a human.

AUTH  Microsoft Graph, delegated, same public client as the functions system.
  One-time: python3 modules/invoices/graph_auth.py
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.invoices.graph_auth import get_token   # noqa: E402

MAILBOX = "accounts@stowawaybar.com"
GRAPH = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(MAILBOX)}"
PROCESSED_FOLDER = "Invoices Processed"
REVIEW_FOLDER = "Invoices Review"
BATCH = 20        # messages per run; the schedule catches the rest
WINDOW_WEEKS = 6  # RECENT only — never reach back further than this (Zak)

# Clearly NOT an invoice — statements, reminders, remittances, receipts. We skip
# these before spending an extraction on them. Conservative on purpose: only
# obvious non-invoices; anything ambiguous still goes through the validator,
# which is the real relevance gate. A real invoice rarely carries these words.
import re as _re  # noqa: E402
SKIP_SUBJECT = _re.compile(
    r"\b(statement|remittance|payment\s+reminder|reminder|overdue|thank\s+you\s+for\s+your\s+payment"
    r"|account\s+balance|past\s+due|receipt\s+of\s+payment)\b", _re.I)


# ── Graph helpers ──────────────────────────────────────────────────────────
def _req(token, method, path, body=None):
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"Graph {e.code} on {method} {url.split('?')[0]}: {body}") from None


def ensure_folder(token, name) -> str:
    """Folder id for `name`, creating it at the mailbox root if missing."""
    q = urllib.parse.quote(f"displayName eq '{name}'")
    found = _req(token, "GET", f"/mailFolders?$filter={q}").get("value", [])
    if found:
        return found[0]["id"]
    return _req(token, "POST", "/mailFolders", {"displayName": name})["id"]


def messages_with_attachments(token, folder="inbox"):
    # RECENT ONLY. Filter on receivedDateTime (indexed -> efficient) for the
    # last WINDOW_WEEKS; check hasAttachments client-side. Filtering on BOTH
    # receivedDateTime and hasAttachments trips Graph's "InefficientFilter", so
    # we don't — and we never reach back past the window regardless of how much
    # history sits in the folder. `folder` is a well-known name (inbox) or a
    # folder id (for the Review-retry pass).
    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=WINDOW_WEEKS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qs = urllib.parse.urlencode({
        "$filter": f"receivedDateTime ge {cutoff}",
        "$select": "id,subject,from,receivedDateTime,hasAttachments",
        "$orderby": "receivedDateTime desc",
        "$top": str(BATCH),
    }, quote_via=urllib.parse.quote)
    msgs = _req(token, "GET", f"/mailFolders/{folder}/messages?{qs}").get("value", [])
    return [m for m in msgs if m.get("hasAttachments")]


def pdf_attachments(token, msg_id):
    out = []
    for a in _req(token, "GET", f"/messages/{msg_id}/attachments").get("value", []):
        name = (a.get("name") or "").lower()
        ctype = (a.get("contentType") or "").lower()
        is_pdf = ctype == "application/pdf" or name.endswith(".pdf")
        if is_pdf and a.get("contentBytes"):
            out.append((a["name"], base64.b64decode(a["contentBytes"])))
    return out


def move_message(token, msg_id, folder_id):
    _req(token, "POST", f"/messages/{msg_id}/move", {"destinationId": folder_id})


# ── invoice handling ───────────────────────────────────────────────────────
def run_invoice(pdf_bytes, source, sender="") -> int:
    """run.py on one PDF. 0 PASS, 2 REVIEW, 1 ERROR (its own exit codes).
    Passes the sender domain so run.py tries a free deterministic parser first."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp = f.name
    try:
        cmd = [sys.executable, "modules/invoices/run.py", "--pdf", tmp, "--source", source]
        if sender:
            cmd += ["--sender", sender.split("@")[-1].lower()]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode == 1:
            print(f"    extract error: {r.stderr.strip()[:200]}")
        return r.returncode
    finally:
        Path(tmp).unlink(missing_ok=True)


def aggregate_and_commit(dry_run: bool):
    subprocess.run([sys.executable, "modules/invoices/build_cogs_list.py"], cwd=ROOT, check=False)
    subprocess.run([sys.executable, "modules/recipes/pipeline/build_costs.py"], cwd=ROOT, check=False)
    # refresh the app's review queue so newly-ingested bills show up at /invoices
    subprocess.run([sys.executable, "modules/invoices/build_invoice_queue.py"], cwd=ROOT, check=False)
    if dry_run:
        return
    subprocess.run(["git", "add", "data/invoices", "data/invoices_review",
                    "data/cogs_list.csv", "data/costs.csv",
                    "dashboard/invoices/queue.json", "dashboard/invoices/accounts.json"], cwd=ROOT, check=False)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode
    if staged == 0:
        print("nothing new to commit")
        return
    subprocess.run(["git", "commit", "-m", "Invoice ingest from accounts@ mailbox"], cwd=ROOT, check=False)
    subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=ROOT, check=False)
    p = subprocess.run(["git", "push"], cwd=ROOT, capture_output=True, text=True)
    print("push:", (p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout).strip() else "ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list only; no extract, move or commit")
    ap.add_argument("--source-folder", default="inbox",
                    help="'inbox' (default) or 'Invoices Review' to re-run stuck ones (retry pass)")
    args = ap.parse_args()
    retry = args.source_folder.lower() != "inbox"   # Review-retry pass

    token = get_token()
    processed_id = review_id = None
    if not args.dry_run:
        processed_id = ensure_folder(token, PROCESSED_FOLDER)
        review_id = ensure_folder(token, REVIEW_FOLDER)
    source = review_id if retry else "inbox"
    msgs = messages_with_attachments(token, source)
    label = "Review folder (retry)" if retry else "accounts@ inbox"
    print(f"{len(msgs)} message(s) with attachments in {label}"
          + (f"  [model={os.environ.get('INVOICE_MODEL','haiku')}]" if retry else ""))
    if not msgs:
        return 0

    any_change = False
    for m in msgs:
        subj = m.get("subject", "(no subject)")
        sender = (m.get("from", {}).get("emailAddress", {}) or {}).get("address", "?")
        # On the first pass, skip obvious non-invoices. On a retry we process
        # everything already in Review (they were flagged for a reason).
        if not retry and SKIP_SUBJECT.search(subj):
            print(f"\n• {subj}  <{sender}>  — skip (statement/reminder, not an invoice)")
            if not args.dry_run:
                move_message(token, m["id"], review_id)
            continue
        pdfs = pdf_attachments(token, m["id"])
        print(f"\n• {subj}  <{sender}>  — {len(pdfs)} PDF(s)")
        if args.dry_run:
            continue
        if not pdfs:
            if not retry:
                move_message(token, m["id"], review_id)   # attachment but no PDF -> human
            continue

        worst = 0
        for name, data in pdfs:
            code = run_invoice(data, f"{subj} / {name}", sender=sender)
            worst = max(worst, 1 if code == 1 else (2 if code == 2 else 0))
            any_change = True
        if worst == 0:
            move_message(token, m["id"], processed_id)    # rescued -> Processed
            print("    -> Processed")
        elif not retry:
            move_message(token, m["id"], review_id)
            print("    -> Review")
        else:
            print("    -> still stuck (left in Review)")   # retry couldn't rescue it

    if any_change or args.dry_run:
        aggregate_and_commit(args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
