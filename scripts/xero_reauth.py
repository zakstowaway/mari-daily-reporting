#!/usr/bin/env python3
"""
Re-authorise Xero with the WIDER scope the invoice pipeline needs.

The original bootstrap only asked for reports + settings (read). To read
historical bill coding and to WRITE draft bills (the Dext replacement), we also
need accounting.transactions and accounting.contacts. This script re-runs the
same loopback OAuth flow with those scopes and overwrites the token cache.

Run on the Mac:   python3 scripts/xero_reauth.py
A browser opens to the Xero consent page — approve, and it writes the new token.
Nothing else changes; xero_pull.py keeps refreshing as before.
"""
import base64
import json
import secrets as pysecrets
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SECRETS_DIR = Path("/Users/stowaway/Documents/STOW/Sales Reports/Daily Reporting/.secrets")
APP_FILE = SECRETS_DIR / "xero_app.json"
CACHE_FILE = SECRETS_DIR / "xero_token_cache.json"
REDIRECT = "http://localhost:8400/callback"
# This app is post-2-Mar-2026, so it only has Xero's NEW GRANULAR scopes (the
# broad accounting.transactions / accounting.reports.read return invalid_scope).
#   accounting.invoices        -> create/read bills (ACCPAY) — the write path
#   accounting.contacts        -> find/create suppliers on a bill
#   accounting.settings.read   -> chart of accounts + tracking categories
#   accounting.reports.profitandloss.read + payroll.* -> keep existing pulls working
SCOPES = ("offline_access "
          "accounting.invoices accounting.contacts accounting.settings.read "
          "accounting.reports.profitandloss.read "
          "payroll.employees.read payroll.payruns.read payroll.payslip.read "
          "payroll.timesheets.read payroll.settings.read")

app = json.loads(APP_FILE.read_text())
state = pysecrets.token_urlsafe(16)
auth_url = ("https://login.xero.com/identity/connect/authorize?" + urllib.parse.urlencode({
    "response_type": "code", "client_id": app["client_id"],
    "redirect_uri": REDIRECT, "scope": SCOPES, "state": state}))

code_holder = {}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("state", [None])[0] != state:
            self.send_response(400); self.end_headers()
            self.wfile.write(b"state mismatch"); return
        code_holder["code"] = (q.get("code") or [None])[0]
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(b"<h2>Xero re-connected with the wider scope. You can close this tab.</h2>")

    def log_message(self, *a):
        pass


Path("/tmp/xero_auth_url.txt").write_text(auth_url)   # so a helper can drive the browser here
print("Opening Xero consent page (approve the extra permissions)…", flush=True)
webbrowser.open(auth_url)
print("If the browser didn't open, visit:\n" + auth_url)
srv = HTTPServer(("localhost", 8400), H)
while "code" not in code_holder:
    srv.handle_request()
srv.server_close()

basic = base64.b64encode(f"{app['client_id']}:{app['client_secret']}".encode()).decode()
req = urllib.request.Request(
    "https://identity.xero.com/connect/token",
    data=urllib.parse.urlencode({"grant_type": "authorization_code",
                                 "code": code_holder["code"], "redirect_uri": REDIRECT}).encode(),
    headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
tok = json.loads(urllib.request.urlopen(req).read())

req = urllib.request.Request("https://api.xero.com/connections",
                             headers={"Authorization": f"Bearer {tok['access_token']}",
                                      "Content-Type": "application/json"})
conns = json.loads(urllib.request.urlopen(req).read())
tenant = next((c for c in conns if "stowaway" in (c.get("tenantName") or "").lower()), conns[0])

CACHE_FILE.write_text(json.dumps({
    "refresh_token": tok["refresh_token"],
    "tenant_id": tenant["tenantId"],
    "tenant_name": tenant.get("tenantName"),
    "scope": SCOPES,
}, indent=2))
print(f"\nToken cache updated: {CACHE_FILE}")
print(f"Tenant: {tenant.get('tenantName')}")
print("Scope now includes accounting.invoices + accounting.contacts (granular).")
print("Next: python3 modules/invoices/learn_coding.py   (learn history)")
print("Then: draft bills can be pushed with modules/invoices/xero_push.py")
