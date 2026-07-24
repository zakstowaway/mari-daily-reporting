#!/usr/bin/env python3
"""Free, always-on replacement for the Pipedream sales-email flow (Gmail/IMAP).

Microsoft 365 in this tenant blocks every self-serve API path (app registration
is admin-only; user consent disabled; Office client not preauthorised). So the
three Lightspeed "Daily Sales Auto" emails are routed to a Gmail and read over
IMAP with a Google app password - no admin, always-on, $0.

Fires the SAME repository_dispatch events the daily pull consumes
(stow-csv-arrived / hg-csv-arrived / insights-csv-arrived), so the whole
downstream pipeline is unchanged.

Dedupe is a committed message-id ledger (.ingest/processed.json), NOT the unread
flag - because this is a personal inbox a human also reads. We fetch with
BODY.PEEK so we never alter read/unread state. Re-runs are no-ops; a late email
is caught next run.

Env:
    GMAIL_ADDRESS        the Gmail the sales emails are routed to
    GMAIL_APP_PASSWORD   a Google App Password (needs 2-Step Verification)
    GH_DISPATCH_PAT      PAT with repo scope (fires repository_dispatch)
    GH_REPO              owner/repo (default zakstowaway/mari-daily-reporting)
    STATE_FILE           ledger path (default .ingest/processed.json)
"""
import base64, email, imaplib, json, os, re, sys, urllib.request
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

GMAIL = os.environ["GMAIL_ADDRESS"].strip()
APP_PW = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "").strip()   # Google shows it space-separated
PAT = os.environ["GH_DISPATCH_PAT"]
REPO = os.environ.get("GH_REPO", "zakstowaway/mari-daily-reporting")
STATE_FILE = os.environ.get("STATE_FILE", ".ingest/processed.json")
SYD = timezone(timedelta(hours=10))   # AEST (fine for date-stamping)

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


def attachment_b64(msg):
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


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {}


def save_state(state):
    cut = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    state = {k: v for k, v in state.items() if v >= cut}
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, "w"), indent=0, sort_keys=True)


def main():
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    M.login(GMAIL, APP_PW)
    M.select("INBOX")
    # Gmail raw search: last 2 days, has attachment, subject mentions a venue.
    typ, data = M.search(None, "X-GM-RAW",
        'newer_than:2d has:attachment (subject:stow OR subject:hg OR subject:harry OR subject:mari)')
    ids = data[0].split() if data and data[0] else []
    state = load_state()
    fired = scanned = 0
    for num in ids:
        # BODY.PEEK -> does NOT set \Seen, so the human's inbox is untouched
        typ, md = M.fetch(num, "(BODY.PEEK[])")
        if typ != "OK" or not md or not md[0]:
            continue
        msg = email.message_from_bytes(md[0][1])
        scanned += 1
        mid = msg.get("Message-ID") or f"uid-{num.decode()}"
        if mid in state:
            continue
        cl = classify(msg.get("Subject", ""))
        if not cl:
            continue
        event, venue = cl
        b64 = attachment_b64(msg)
        if not b64:
            print(f"  skip '{msg.get('Subject')}' - no csv/zip attachment")
            continue
        tdate = target_date(msg)
        dispatch(event, venue, b64, tdate)
        state[mid] = datetime.now(timezone.utc).isoformat()
        fired += 1
        print(f"  dispatched {event} ({venue}) for {tdate} from '{msg.get('Subject')}'")
    save_state(state)
    M.logout()
    print(f"done - {fired} Insights email(s) ingested, {scanned} candidate(s) scanned")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", type(e).__name__, str(e)[:300]); sys.exit(1)
