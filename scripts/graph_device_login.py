#!/usr/bin/env python3
"""ONE-TIME device-code login for the Insights email poller.

Run this once, on your own machine, signed in as the mailbox that receives the
Lightspeed "Daily Sales Auto" emails. It mints a delegated refresh token for
Microsoft's first-party "Microsoft Graph Command Line Tools" public client
(scope: Mail.Read) and stores it as the GitHub Actions secret GRAPH_REFRESH_TOKEN.

No Azure app registration, no tenant admin. Nothing here touches Anthropic/Claude
- the token only ever exists on this machine and in your repo secrets.

Usage:
    python3 scripts/graph_device_login.py

Requirements: python3 (built in on macOS). Optional: GitHub CLI `gh` (brew install
gh) so the secret is set automatically. Without gh, it prints the token for you to
paste into GitHub -> repo Settings -> Secrets and variables -> Actions.
"""
import json, time, urllib.parse, urllib.request, urllib.error, subprocess, shutil, sys

CLIENT = "d3590ed6-52b3-4102-aeff-aad2292ab01c"   # Microsoft Office (public, pre-approved in this tenant - same client the functions auto-draft uses)
TENANT = "organizations"
SCOPE = "offline_access Mail.Read"
REPO = "zakstowaway/mari-daily-reporting"
AUTH = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"


def post(path, params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(AUTH + path, data=data)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def main():
    dc = post("/devicecode", {"client_id": CLIENT, "scope": SCOPE})
    if "user_code" not in dc:
        print("Failed to start device login:", dc); sys.exit(1)
    print("\n" + "=" * 60)
    print("  1. Open:  " + dc["verification_uri"])
    print("  2. Enter code:  " + dc["user_code"])
    print("  3. Sign in as the mailbox that receives the sales emails,")
    print("     and approve the 'read your mail' consent.")
    print("=" * 60 + "\n  waiting for you to finish...", flush=True)

    interval = int(dc.get("interval", 5))
    while True:
        time.sleep(interval)
        tok = post("/token", {"client_id": CLIENT, "grant_type": "device_code",
                              "device_code": dc["device_code"]})
        err = tok.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5; continue
        if err:
            print("Login error:", err, tok.get("error_description", "")[:200]); sys.exit(1)
        break

    rt = tok.get("refresh_token")
    if not rt:
        print("No refresh token returned (need offline_access). Response:", tok); sys.exit(1)
    print("\n[OK] Logged in. Refresh token minted.\n")

    if shutil.which("gh"):
        try:
            subprocess.run(["gh", "secret", "set", "GRAPH_REFRESH_TOKEN",
                            "--repo", REPO, "--body", rt], check=True)
            print(f"[OK] Stored as GRAPH_REFRESH_TOKEN secret on {REPO}.")
            print("     Also confirm GH_DISPATCH_PAT exists (it already should).")
            print("\nAll done. Trigger the 'Ingest Insights Email' workflow to test.")
            return
        except Exception as e:
            print("gh secret set failed:", e)

    print("gh CLI not found (or failed). Set the secret manually:")
    print(f"  {REPO}  ->  Settings  ->  Secrets and variables  ->  Actions  ->  New secret")
    print("  Name:  GRAPH_REFRESH_TOKEN")
    print("  Value (copy the line below):\n")
    print(rt + "\n")


if __name__ == "__main__":
    main()
