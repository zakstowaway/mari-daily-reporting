#!/usr/bin/env python3
"""
One-time Microsoft Graph auth for the invoice mailbox (accounts@stowawaybar.com).

    python3 modules/invoices/graph_auth.py

Same public client as the functions auto-draft system — no Azure app to
register. You sign in ONCE via device code; after that the token refreshes
silently (like xero_pull's rotating token), so the poller runs unattended.

Sign in as the account that can READ accounts@stowawaybar.com:
  * if accounts@ is its own licensed mailbox -> sign in as accounts@;
  * if it's a shared mailbox -> sign in as a user who has Full Access to it
    (the poller reads /users/accounts@stowawaybar.com either way).

Token cache is kept SEPARATE from the functions one so the two don't fight over
which account is signed in.
"""
import os
import msal

CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"        # Microsoft Office public client
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["Mail.ReadWrite"]
CACHE = os.environ.get(
    "GRAPH_INVOICE_TOKEN_CACHE",
    os.path.expanduser("~/Documents/STOW/.graph_token_cache_invoices.json"),
)


def get_token(interactive: bool = False) -> str:
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE):
        cache.deserialize(open(CACHE).read())
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)

    accounts = app.get_accounts()
    if accounts:
        r = app.acquire_token_silent(SCOPES, account=accounts[0])
        if r and "access_token" in r:
            if cache.has_state_changed:
                open(CACHE, "w").write(cache.serialize())
            return r["access_token"]
    if not interactive:
        raise RuntimeError("No cached invoice-mailbox token — run: python3 modules/invoices/graph_auth.py")

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"device flow failed: {flow}")
    print("\n" + "=" * 60)
    print("AUTHORISE THE INVOICE MAILBOX — one time")
    print("=" * 60)
    print(f"\n1. Go to:  {flow['verification_uri']}")
    print(f"2. Enter:  {flow['user_code']}")
    print("\n   Sign in as accounts@stowawaybar.com (or a user with Full")
    print("   Access to it).")
    print("=" * 60 + "\n")
    r = app.acquire_token_by_device_flow(flow)
    if "access_token" not in r:
        raise RuntimeError(f"auth failed: {r.get('error_description')}")
    open(CACHE, "w").write(cache.serialize())
    print(f"\n✅ Authenticated. Token cached at {CACHE}")
    return r["access_token"]


if __name__ == "__main__":
    import json
    import urllib.request
    tok = get_token(interactive=True)
    req = urllib.request.Request("https://graph.microsoft.com/v1.0/me",
                                 headers={"Authorization": f"Bearer {tok}"})
    me = json.loads(urllib.request.urlopen(req).read())
    print(f"Signed in as: {me.get('displayName')} ({me.get('mail') or me.get('userPrincipalName')})")
