#!/usr/bin/env python3
"""
Manage people. One username per person, so every write has a name on it.

    python3 -m modules.auth.cli add sam --name "Sam Taylor" --role stowfood
    python3 -m modules.auth.cli list
    python3 -m modules.auth.cli passwd sam
    python3 -m modules.auth.cli disable sam
    python3 -m modules.auth.cli secret            # what to paste into the Worker

TWO FILES, AND THE SPLIT IS THE POINT
-------------------------------------
    data/people.json        PUBLIC. username -> name, role, venue, active.
                            Ships to the browser. Nothing secret in it. It is
                            what the app uses to show "signed in as Sam Taylor".

    .secrets/passwords.json PRIVATE. username -> pbkdf2 hash.
                            GITIGNORED. Never committed, never served. Uploaded
                            to the Worker once as a secret:
                                wrangler secret put PASSWORDS < .secrets/passwords.json

The old dashboard/users.json put BOTH in one file and served it to the browser,
salt and all. That is the thing being fixed.

WHY PER-PERSON, NOT PER-STATION
-------------------------------
Zak, 2026-07-17: "one username per person, so that we can see who's inputting
data". Today a recipe entered by 'stowfood' could be any of six people. With a
person per login, the write is committed AS them and `git log data/recipes/`
becomes the audit trail. No extra system, no audit table — git already does it.

Roles stay (they gate what you can see). They are just no longer identities.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.auth.passwords import hash_password  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PEOPLE = ROOT / "data" / "people.json"
SECRETS = ROOT / ".secrets" / "passwords.json"

# Roles gate visibility. Carried over from users.json so nothing breaks.
ROLES = {
    "admin":    "Everything",
    "bigchef":  "All kitchens",
    "stowfood": "Stowaway kitchen",
    "hgfood":   "Harry Gatos kitchen",
    "bar":      "Bar / FOH (Stow + HG)",
    "pizza":    "Marilyna's",
}


def _load(p: Path, default):
    if not p.exists():
        return default
    return json.loads(p.read_text())


def _save_people(d):
    PEOPLE.parent.mkdir(parents=True, exist_ok=True)
    PEOPLE.write_text(json.dumps(d, indent=2) + "\n")


def _save_secrets(d):
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    SECRETS.write_text(json.dumps(d, indent=2) + "\n")
    SECRETS.chmod(0o600)


def cmd_add(a) -> int:
    people = _load(PEOPLE, {"people": {}})
    secrets = _load(SECRETS, {})
    if a.username in people["people"]:
        print(f"{a.username} already exists — use passwd to change the password")
        return 1
    if a.role not in ROLES:
        print(f"unknown role {a.role!r}. One of: {', '.join(ROLES)}")
        return 1

    pw = getpass.getpass(f"password for {a.username}: ")
    if pw != getpass.getpass("again: "):
        print("passwords do not match")
        return 1

    people["people"][a.username] = {
        "name": a.name,
        "role": a.role,
        "venue": a.venue,
        "active": True,
        "added": datetime.now().date().isoformat(),
    }
    secrets[a.username] = hash_password(pw)
    _save_people(people)
    _save_secrets(secrets)
    print(f"added {a.username} ({a.name}, {a.role})")
    print(f"  data/people.json      updated — commit this")
    print(f"  .secrets/passwords.json updated — NOT committed; push to the Worker:")
    print(f"    wrangler secret put PASSWORDS < .secrets/passwords.json")
    return 0


def cmd_passwd(a) -> int:
    people = _load(PEOPLE, {"people": {}})
    if a.username not in people["people"]:
        print(f"no such person: {a.username}")
        return 1
    secrets = _load(SECRETS, {})
    pw = getpass.getpass(f"new password for {a.username}: ")
    if pw != getpass.getpass("again: "):
        print("passwords do not match")
        return 1
    secrets[a.username] = hash_password(pw)
    _save_secrets(secrets)
    print(f"password changed. Push to the Worker:")
    print(f"  wrangler secret put PASSWORDS < .secrets/passwords.json")
    return 0


def cmd_disable(a) -> int:
    """
    Deactivate, don't delete. Their name stays readable on everything they
    entered — that is the point of per-person logins.
    """
    people = _load(PEOPLE, {"people": {}})
    if a.username not in people["people"]:
        print(f"no such person: {a.username}")
        return 1
    people["people"][a.username]["active"] = False
    people["people"][a.username]["disabled"] = datetime.now().date().isoformat()
    _save_people(people)
    secrets = _load(SECRETS, {})
    secrets.pop(a.username, None)          # credential gone, identity kept
    _save_secrets(secrets)
    print(f"{a.username} disabled. History keeps their name; they can't sign in.")
    print(f"  wrangler secret put PASSWORDS < .secrets/passwords.json")
    return 0


def cmd_list(a) -> int:
    people = _load(PEOPLE, {"people": {}})["people"]
    secrets = _load(SECRETS, {})
    if not people:
        print("nobody yet — add someone:")
        print('  python3 -m modules.auth.cli add sam --name "Sam Taylor" --role stowfood')
        return 0
    print(f"{'username':<12} {'name':<22} {'role':<10} {'venue':<12} active  pw")
    for u, p in sorted(people.items()):
        print(f"{u:<12} {p['name']:<22} {p['role']:<10} {str(p.get('venue') or '-'):<12} "
              f"{str(p.get('active', True)):<7} {'yes' if u in secrets else 'MISSING'}")
    missing = [u for u, p in people.items() if p.get("active", True) and u not in secrets]
    if missing:
        print(f"\nno password set (cannot sign in): {', '.join(missing)}")
    return 0


def cmd_secret(a) -> int:
    """Print what goes into the Worker. Never printed with people.json."""
    secrets = _load(SECRETS, {})
    if not secrets:
        print("no passwords set yet", file=sys.stderr)
        return 1
    print(json.dumps(secrets, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Manage people who can sign in.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="add a person")
    p.add_argument("username")
    p.add_argument("--name", required=True, help='real name, e.g. "Sam Taylor"')
    p.add_argument("--role", required=True, choices=list(ROLES))
    p.add_argument("--venue", default=None, choices=["stowaway", "harry", "marilynas", None])
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("passwd", help="change someone's password")
    p.add_argument("username"); p.set_defaults(fn=cmd_passwd)

    p = sub.add_parser("disable", help="stop someone signing in, keep their history")
    p.add_argument("username"); p.set_defaults(fn=cmd_disable)

    sub.add_parser("list", help="who exists").set_defaults(fn=cmd_list)
    sub.add_parser("secret", help="print the Worker secret").set_defaults(fn=cmd_secret)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
