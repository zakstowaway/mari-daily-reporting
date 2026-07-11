"""
Manage dashboard users — add, change password, remove.

Usage:
  python3 scripts/manage_users.py add <username> <role> "<display>"
  python3 scripts/manage_users.py setpw <username>
  python3 scripts/manage_users.py remove <username>
  python3 scripts/manage_users.py list

Roles: owner, kitchen, delivery

After making changes, commit + push. The dashboard reads users.json on next load.
"""
import hashlib, json, sys, getpass
from pathlib import Path

USERS_FILE = Path(__file__).resolve().parent.parent / "dashboard" / "users.json"

def load():
    with USERS_FILE.open() as f:
        return json.load(f)

def save(cfg):
    with USERS_FILE.open("w") as f:
        json.dump(cfg, f, indent=2)
    print(f"Saved {USERS_FILE}")

def sha256(salt, pw):
    return hashlib.sha256((salt + pw).encode()).hexdigest()

def cmd_add(username, role, display):
    cfg = load()
    if username in cfg["users"]:
        print(f"User '{username}' already exists — use setpw to change password"); return
    if role not in ("owner", "kitchen", "delivery"):
        print(f"Invalid role '{role}' — must be one of: owner, kitchen, delivery"); return
    pw = getpass.getpass(f"Password for {username}: ")
    pw2 = getpass.getpass("Confirm: ")
    if pw != pw2: print("Passwords don't match"); return
    cfg["users"][username] = {"hash": sha256(cfg["salt"], pw), "role": role, "display": display}
    save(cfg)
    print(f"Added {username} ({role})")

def cmd_setpw(username):
    cfg = load()
    if username not in cfg["users"]:
        print(f"User '{username}' not found"); return
    pw = getpass.getpass(f"New password for {username}: ")
    pw2 = getpass.getpass("Confirm: ")
    if pw != pw2: print("Passwords don't match"); return
    cfg["users"][username]["hash"] = sha256(cfg["salt"], pw)
    save(cfg)
    print(f"Changed password for {username}")

def cmd_remove(username):
    cfg = load()
    if username not in cfg["users"]:
        print(f"User '{username}' not found"); return
    del cfg["users"][username]
    save(cfg)
    print(f"Removed {username}")

def cmd_list():
    cfg = load()
    for u, meta in cfg["users"].items():
        print(f"  {u:12s} {meta['role']:10s} {meta['display']}")

def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "add" and len(sys.argv) == 5:
        cmd_add(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "setpw" and len(sys.argv) == 3:
        cmd_setpw(sys.argv[2])
    elif cmd == "remove" and len(sys.argv) == 3:
        cmd_remove(sys.argv[2])
    elif cmd == "list":
        cmd_list()
    else:
        print(__doc__)

if __name__ == "__main__":
    main()
