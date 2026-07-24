#!/usr/bin/env python3
"""Free, always-on replacement for the Pipedream sales-email flow (Gmail/IMAP).

Microsoft 365 in this tenant blocks every self-serve API path (app registration
is admin-only; user consent is disabled; the Office client isn't preauthorised).
So instead of reading M365 we route the three Lightspeed "Daily Sales Auto"
emails to a dedicated free Gmail and read THAT over IMAP with a Google app
password - no admin anywhere, always-on, $0.

It fires the SAME repository_dispatch events the daily pull already consumes
(stow-csv-arrived / hg-csv-arrived / insights-csv-arrived), so the whole
downstream pipeline is unchanged. Runs in GitHub Actions on a morning schedule.

Dedupe: only UNSEEN inbox mail is processed, then marked \\Seen - so re-runs are
no-ops and a late email is caught next run. Uses a dedicated Gmail nobody reads,
so "unseen" is reliable.

Env:
    GMAIL_ADDRESS        the dedicated Gmail the sales emails are routed to
    GMAIL_APP_PASSWORD   a Google App Password (16 chars; needs 2-Step Verification)
    GH_DISPATCH_PAT      PAT with repo scope (fires repository_dispatch)
    GH_REPO              owner/repo (default zakstowaway/mari-daily-reporting)
"""
import base64, email, imaplib, json, os, re, sys, urllib.request
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

GMAIL = os.environ["GMAIL_ADDRESS"]
APP_PW = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")   # Google prints it space-separated
PAT = os.environ["GH_DISPATCH_PAT"]
REPO = os.environ.get("GH_REPO", "zakstowaway/mari-daily-reporting")
SYD = timezone(timedelta(hours=10))   # AEST (fine for date-stamping)

# Subject -> (dispatch event, venue). The three daily CSV auto-exports.
SUBJECT_MAP = [
    (re.compile(r"\bstow\b", re.I),        ("stow-csv-arrived",     "stowaway")),
    (re.compile(r"\b(hg|harry)\b", re.I),  ("hg-csv-arrived",       "harry")),
    (re.compile(r"\bmari", re.I),          ("insights-csv-arrived", "marilynas")),
]


def classify(subject):
    for rx, out in SUBJECT_MAP:
        if rx.search(subject or ""):
            return out
    return None


def find_attachment_b64(msg):
    """Return base64 str of the first .zip/.csv attachment (raw file bytes)."""
    for part in msg.walk():
        fn = part.get_filename() or ""
        if fn.lower().endswith((".zip", ".csv")):
            raw = part.get_payload(decode=True)
            if raw:
                return base64.b64encode(raw).decode()
    return None


def target_date(msg):
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)
    return (dt.astimezone(SYD) - timedelta(days=1)).strftime("%Y-%m-%d")   # report = "Yesterday"


def dispatch(event, venue, csv_b64, tdate):
    payload = {"event_type": event,
               "client_payload": {"venue": venue, "csv_base64": csv_b64,
                                  "target_date": tdate, "source": "gmail-imap-poller"}}
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/dispatches",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"token {PAT}", "Accept": "application/vnd.github+json"})
    urllib.request.urlopen(req, timeout=30)


def main():
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    M.login(GMAIL, APP_PW)
    M.select("INBOX")
    typ, data = M.search(None, "UNSEEN")
    ids = data[0].split() if data and data[0] else []
    fired = scanned = 0
    for num in ids:
        typ, md = M.fetch(num, "(RFC822)")
        if typ != "OK" or not md or not md[0]:
            continue
        msg = email.message_from_bytes(md[0][1])
        scanned += 1
        cl = classify(msg.get("Subject", ""))
        if not cl:
            continue
        event, venue = cl
        b64 = find_attachment_b64(msg)
        if not b64:
            print(f"  skip '{msg.get('Subject')}' - no csv/zip attachment")
            continue
        dispatch(event, venue, b64, target_date(msg))
        M.store(num, "+FLAGS", "\\Seen")
        fired += 1
        print(f"  dispatched {event} ({venue}) for {target_date(msg)} from '{msg.get('Subject')}'")
    M.logout()
    print(f"done - {fired} Insights email(s) ingested, {scanned} unseen scanned")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", type(e).__name__, str(e)[:300]); sys.exit(1)
