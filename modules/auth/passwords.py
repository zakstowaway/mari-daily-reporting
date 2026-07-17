"""
Password hashing. Shared definition, so Python (which creates hashes) and the
Worker (which verifies them) cannot drift apart.

    pbkdf2$sha256$600000$<salt_b64>$<hash_b64>

WHY PBKDF2 AND NOT BCRYPT/ARGON2
--------------------------------
The verifier is a Cloudflare Worker. Workers give you Web Crypto, which has
PBKDF2 built in and does NOT have bcrypt or argon2 (those need a WASM build or
a native module). PBKDF2-HMAC-SHA256 at 600k iterations is OWASP's current
floor and is honest for this job: a handful of kitchen accounts behind a login
that only gates a recipe app. Argon2 would be better and is not worth a WASM
dependency in a Worker for six chefs.

WHAT CHANGED FROM users.json
----------------------------
The old scheme was sha256(one_global_salt + password), computed IN THE BROWSER,
with the salt and every hash shipped to the client. That is:

  * one salt for everyone      -> one rainbow table does the lot
  * unsalted-per-user          -> identical passwords give identical hashes
  * a single fast SHA-256      -> a GPU tries billions/sec
  * verified client-side       -> devtools skips the check entirely

Now: a random 16-byte salt PER USER, 600k iterations, verified server-side, and
the hashes never leave the Worker. The browser only ever sees data/people.json,
which has names and roles in it and nothing secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

ALGO = "pbkdf2"
DIGEST = "sha256"
ITERATIONS = 600_000          # OWASP 2023 floor for PBKDF2-HMAC-SHA256
SALT_BYTES = 16
KEY_BYTES = 32


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def hash_password(password: str, *, salt: bytes | None = None,
                  iterations: int = ITERATIONS) -> str:
    """-> 'pbkdf2$sha256$600000$<salt>$<hash>'. Salt is random per user."""
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = salt or os.urandom(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(DIGEST, password.encode(), salt, iterations, KEY_BYTES)
    return f"{ALGO}${DIGEST}${iterations}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time compare. Used by tests; the Worker has its own copy in JS."""
    try:
        algo, digest, iters, salt_b64, hash_b64 = stored.split("$")
    except ValueError:
        return False
    if algo != ALGO or digest != DIGEST:
        return False
    dk = hashlib.pbkdf2_hmac(digest, password.encode(), _unb64(salt_b64),
                             int(iters), len(_unb64(hash_b64)))
    return hmac.compare_digest(dk, _unb64(hash_b64))
