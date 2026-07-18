#!/usr/bin/env python3
"""
Set a user's ROLE and VENUE in Supabase. Admin-only.

    SUPABASE_URL=https://<ref>.supabase.co \
    SUPABASE_SERVICE_KEY=<service_role key> \
    python3 -m modules.auth.set_role sam@stowawaybar.com --role stowfood --venue stowaway

WHY THIS EXISTS
---------------
Passwords are the user's own business now (Supabase: signup, forgot-password,
reset). But ROLE is not self-service — you don't let a chef self-assign admin.
Role + venue live in the user's app_metadata, which only the SERVICE key can
write, and which rides in the token so the recipe endpoint can trust it.

The service key is a SECRET — it can do anything to your Supabase project. It
is passed via env var and never stored. Get it from:
    Supabase -> Project Settings -> API -> service_role  (NOT the anon key)

Roles: admin, bigchef, stowfood, hgfood, bar, pizza.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

ROLES = ["admin", "bigchef", "stowfood", "hgfood", "bar", "pizza"]
VENUES = ["stowaway", "harry_gatos", "marilynas"]


def _api(url: str, key: str, method: str, path: str, body=None):
    req = urllib.request.Request(
        f"{url}{path}", method=method,
        headers={"apikey": key, "authorization": f"Bearer {key}",
                 "content-type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("email")
    ap.add_argument("--role", required=True, choices=ROLES)
    ap.add_argument("--venue", default=None, choices=VENUES + [None])
    ap.add_argument("--name", default=None, help="display name, optional")
    args = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_KEY (service_role, not anon).",
              file=sys.stderr)
        return 1

    # find the user by email
    users = _api(url, key, "GET", f"/auth/v1/admin/users?email={args.email}")
    lst = users.get("users") or users.get("data") or []
    match = [u for u in lst if u.get("email", "").lower() == args.email.lower()]
    if not match:
        print(f"No Supabase user with email {args.email}. They must sign up first.",
              file=sys.stderr)
        return 1
    uid = match[0]["id"]
    app_meta = dict(match[0].get("app_metadata") or {})
    app_meta["role"] = args.role
    if args.venue:
        app_meta["venue"] = args.venue
    payload = {"app_metadata": app_meta}
    if args.name:
        payload["user_metadata"] = {**(match[0].get("user_metadata") or {}), "name": args.name}

    _api(url, key, "PUT", f"/auth/v1/admin/users/{uid}", payload)
    print(f"{args.email}: role={args.role}"
          + (f" venue={args.venue}" if args.venue else "")
          + ". They'll get it on their next sign-in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
