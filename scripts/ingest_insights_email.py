#!/usr/bin/env python3
"""Free, always-on replacement for the Pipedream sales-email flow.

Reads the Lightspeed Insights "Daily Sales Auto" emails straight from the
Microsoft 365 mailbox via the Graph API, and fires the SAME repository_dispatch
events the existing daily pull already consumes (stow-csv-arrived /
hg-csv-arrived / insights-csv-arrived). So it is a drop-in for Pipedream — the
whole downstream pipeline (Deputy pull, cross-till split, Mari carve, aggregate,
commit, deploy) is unchanged. Runs in GitHub Actions on a morning schedule.

Cost: $0. Uses Microsoft 365 (already paid) + GitHub Actions minutes.

Auth: app-only client credentials (no token rotation to babysit — unlike the
Xero pull). Env:
    GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET  (Azure app registration)
    INSIGHTS_MAILBOX      the address the Lightspeed schedules email to (e.g. hello@stowawaybar.com)
    GH_DISPATCH_PAT       a PAT with repo scope (fires repository_dispatch; GITHUB_TOKEN can't)
    GH_REPO               owner/repo (default zakstowaway/mari-daily-reporting)

Idempotent: only processes UNREAD Insights emails and marks them read, so a
re-run does nothing. A late email is caught on the next scheduled run (the poll
model self-heals — unlike Pipedream, which drops an event if it can't run).
"""
import base64, json, os, re, sys, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

TENANT = os.environ["GRAPH_TENANT_ID"]
CLIENT = os.environ["GRAPH_CLIENT_ID"]
SECRET = os.environ["GRAPH_CLIENT_SECRET"]
MAILBOX = os.environ["INSIGHTS_MAILBOX"]
PAT = os.environ["GH_DISPATCH_PAT"]
REPO = os.environ.get("GH_REPO", "zakstowaway/mari-daily-reporting")
SYD = timezone(timedelta(hours=10))   # AEST (close enough for date-stamping)

# Subject -> (dispatch event, venue). The three daily CSV auto-exports.
SUBJECT_MAP = [
    (re.compile(r"\bstow\b", re.I),        ("stow-csv-arrived",     "stowaway")),
    (re.compile(r"\b(hg|harry)\b", re.I),  ("hg-csv-arrived",       "harry")),
    (re.compile(r"\bmari", re.I),          ("insights-csv-arrived", "marilynas")),
]


def graph_token():
    body = urllib.parse.urlencode({
        "client_id": CLIENT, "client_secret": SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials"}).encode()
    r = urllib.request.Request(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token", data=body)
    return json.loads(urllib.request.urlopen(r, timeout=30).read())["access_token"]


def graph(tok, path, method="GET", data=None):
    url = "https://graph.microsoft.com/v1.0" + path
    req = urllib.request.Request(url, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def classify(subject):
    for rx, out in SUBJECT_MAP:
        if rx.search(subject or ""):
            return out
    return None


def dispatch(event, venue, csv_b64, target_date):
    payload = {"event_type": event,
               "client_payload": {"venue": venue, "csv_base64": csv_b64,
                                  "target_date": target_date, "source": "m365-graph-poller"}}
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/dispatches",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"token {PAT}", "Accept": "application/vnd.github+json"})
    urllib.request.urlopen(req, timeout=30)


def main():
    tok = graph_token()
    # Unread, has-attachment mail in the inbox, newest first. We filter subject in
    # code (Graph $filter can't do 'contains' on subject).
    q = ("/users/%s/mailFolders/inbox/messages?"
         "$filter=isRead eq false and hasAttachments eq true"
         "&$select=id,subject,receivedDateTime&$orderby=receivedDateTime desc&$top=25"
         % urllib.parse.quote(MAILBOX))
    msgs = graph(tok, q).get("value", [])
    fired = 0
    for m in msgs:
        cl = classify(m.get("subject", ""))
        if not cl:
            continue
        event, venue = cl
        # report filter is "Yesterday" -> data date = received day (Sydney) - 1
        rcv = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")).astimezone(SYD)
        target = (rcv - timedelta(days=1)).strftime("%Y-%m-%d")
        # grab the CSV/zip attachment
        atts = graph(tok, f"/users/{urllib.parse.quote(MAILBOX)}/messages/{m['id']}/attachments"
                          "?$select=name,contentType,contentBytes").get("value", [])
        att = next((a for a in atts if (a.get("name") or "").lower().endswith((".zip", ".csv"))), None)
        if not att or not att.get("contentBytes"):
            print(f"  skip '{m['subject']}' — no csv/zip attachment")
            continue
        # contentBytes is already base64 of the raw file (zip or csv); the daily
        # pull's ingest step handles zip-or-csv, so pass it straight through.
        dispatch(event, venue, att["contentBytes"], target)
        graph(tok, f"/users/{urllib.parse.quote(MAILBOX)}/messages/{m['id']}",
              method="PATCH", data={"isRead": True})
        fired += 1
        print(f"  dispatched {event} ({venue}) for {target} from '{m['subject']}'")
    print(f"done — {fired} Insights email(s) ingested, {len(msgs)} unread w/attachment scanned")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:300]); sys.exit(1)
