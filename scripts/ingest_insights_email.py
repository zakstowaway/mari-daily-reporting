#!/usr/bin/env python3
"""Free, always-on replacement for the Pipedream sales-email flow.

Reads the Lightspeed Insights "Daily Sales Auto" emails straight from the
Microsoft 365 mailbox via the Graph API, and fires the SAME repository_dispatch
events the daily pull already consumes (stow-csv-arrived / hg-csv-arrived /
insights-csv-arrived). Drop-in for Pipedream: the whole downstream pipeline
(Deputy pull, cross-till split, Mari carve, aggregate, commit, deploy) is
unchanged. Runs in GitHub Actions on a morning schedule.

Cost: $0. Uses Microsoft 365 (already paid) + GitHub Actions minutes.

AUTH — delegated device-code (NO Azure app registration, NO tenant admin).
We use Microsoft's first-party public client "Microsoft Graph Command Line
Tools" and a long-lived refresh token minted once by scripts/graph_device_login.py
(run by a human, consenting to read their OWN mailbox). Each run swaps the
refresh token for an access token, reads /me mail, and — because Entra rotates
refresh tokens — writes the fresh refresh token to REFRESH_OUT so the workflow
can push it back into the GRAPH_REFRESH_TOKEN secret. That keeps it alive
indefinitely; if writeback is skipped the token still lasts ~90 days.

Env:
    GRAPH_CLIENT_ID     public client id (default = MS Graph CLI, 14d82eec-...)
    GRAPH_TENANT_ID     tenant id or "organizations" (default "organizations")
    GRAPH_REFRESH_TOKEN the delegated refresh token (from graph_device_login.py)
    GH_DISPATCH_PAT     PAT with repo scope (fires repository_dispatch)
    GH_REPO             owner/repo (default zakstowaway/mari-daily-reporting)
    REFRESH_OUT         path to write the rotated refresh token (default /tmp/new_refresh_token.txt)
    STATE_FILE          processed-id ledger (default .ingest/processed.json)

Read-only (Mail.Read) so we can't mark mail read; instead we dedupe on Graph
message id in STATE_FILE (committed by the workflow). Poll model self-heals: a
late email is caught on the next run; re-runs are no-ops.
"""
import base64, json, os, re, sys, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

CLIENT = os.environ.get("GRAPH_CLIENT_ID", "14d82eec-204b-4c2f-b7e8-296a70dab67e")
TENANT = os.environ.get("GRAPH_TENANT_ID", "organizations")
REFRESH = os.environ["GRAPH_REFRESH_TOKEN"]
PAT = os.environ["GH_DISPATCH_PAT"]
REPO = os.environ.get("GH_REPO", "zakstowaway/mari-daily-reporting")
REFRESH_OUT = os.environ.get("REFRESH_OUT", "/tmp/new_refresh_token.txt")
STATE_FILE = os.environ.get("STATE_FILE", ".ingest/processed.json")
SCOPE = "offline_access Mail.Read"
SYD = timezone(timedelta(hours=10))   # AEST (fine for date-stamping)

# Subject -> (dispatch event, venue). The three daily CSV auto-exports.
SUBJECT_MAP = [
    (re.compile(r"\bstow\b", re.I),        ("stow-csv-arrived",     "stowaway")),
    (re.compile(r"\b(hg|harry)\b", re.I),  ("hg-csv-arrived",       "harry")),
    (re.compile(r"\bmari", re.I),          ("insights-csv-arrived", "marilynas")),
]


def refresh_access_token():
    """Swap the refresh token for an access token; persist the rotated refresh
    token (Entra returns a new one) so the next run stays authenticated."""
    body = urllib.parse.urlencode({
        "client_id": CLIENT, "grant_type": "refresh_token",
        "refresh_token": REFRESH, "scope": SCOPE}).encode()
    r = urllib.request.Request(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token", data=body)
    tok = json.loads(urllib.request.urlopen(r, timeout=30).read())
    new_rt = tok.get("refresh_token")
    if new_rt and new_rt != REFRESH:
        try:
            with open(REFRESH_OUT, "w") as f:
                f.write(new_rt)
            print(f"  rotated refresh token -> {REFRESH_OUT}")
        except Exception as e:
            print(f"  WARN could not write rotated token: {e}")
    return tok["access_token"]


def graph(tok, path):
    req = urllib.request.Request("https://graph.microsoft.com/v1.0" + path,
        headers={"Authorization": f"Bearer {tok}"})
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
                                  "target_date": target_date, "source": "m365-device-poller"}}
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
    # prune ids older than 7 days so the ledger stays small
    cut = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    state = {k: v for k, v in state.items() if v >= cut}
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, "w"), indent=0, sort_keys=True)


def main():
    tok = refresh_access_token()
    state = load_state()
    # Own mailbox (delegated /me), attachments, last 2 days, newest first. Subject
    # is filtered in code (Graph $filter has no 'contains' on subject).
    since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = ("/me/mailFolders/inbox/messages?"
         f"$filter=hasAttachments eq true and receivedDateTime ge {since}"
         "&$select=id,subject,receivedDateTime&$orderby=receivedDateTime desc&$top=50")
    msgs = graph(tok, q).get("value", [])
    fired = 0
    for m in msgs:
        if m["id"] in state:
            continue
        cl = classify(m.get("subject", ""))
        if not cl:
            continue
        event, venue = cl
        rcv = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")).astimezone(SYD)
        target = (rcv - timedelta(days=1)).strftime("%Y-%m-%d")   # report filter = "Yesterday"
        atts = graph(tok, f"/me/messages/{m['id']}/attachments"
                          "?$select=name,contentType,contentBytes").get("value", [])
        att = next((a for a in atts if (a.get("name") or "").lower().endswith((".zip", ".csv"))), None)
        if not att or not att.get("contentBytes"):
            print(f"  skip '{m['subject']}' - no csv/zip attachment")
            continue
        dispatch(event, venue, att["contentBytes"], target)
        state[m["id"]] = m["receivedDateTime"]
        fired += 1
        print(f"  dispatched {event} ({venue}) for {target} from '{m['subject']}'")
    save_state(state)
    print(f"done - {fired} Insights email(s) ingested, {len(msgs)} recent w/attachment scanned")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:400]); sys.exit(1)
