"""
Password hashing tests.

The one that matters is test_worker_js_verifies_python_hashes: Python creates
the hashes, the Worker verifies them. If they drift, nobody can sign in and the
failure looks like "wrong password" for everyone at once.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.auth.passwords import ITERATIONS, hash_password, verify_password  # noqa: E402


def test_round_trip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h)


def test_wrong_password_fails():
    h = hash_password("correct horse battery staple")
    assert not verify_password("Correct horse battery staple", h)
    assert not verify_password("", h)


def test_same_password_gives_different_hashes():
    """
    Per-user random salt. The OLD scheme used ONE global salt for everyone
    (users.json: 'mari-2026-northern-beaches'), so two people with the same
    password had the same hash, and one rainbow table did the lot.
    """
    a = hash_password("same-password-123")
    b = hash_password("same-password-123")
    assert a != b
    assert verify_password("same-password-123", a)
    assert verify_password("same-password-123", b)


def test_format_is_what_the_worker_expects():
    algo, digest, iters, salt, h = hash_password("abcdefgh").split("$")
    assert (algo, digest) == ("pbkdf2", "sha256")
    assert int(iters) == ITERATIONS >= 600_000      # OWASP floor
    assert len(salt) > 0 and len(h) > 0


def test_short_passwords_refused():
    with pytest.raises(ValueError, match="at least 8"):
        hash_password("short")


def test_garbage_stored_value_is_false_not_an_exception():
    for junk in ("", "nonsense", "pbkdf2$sha256$bad", "md5$x$1$a$b"):
        assert verify_password("anything", junk) is False


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_worker_js_verifies_python_hashes(tmp_path):
    """
    THE ONE THAT MATTERS.

    Python writes the hash; the Worker reads it. Two languages, two crypto
    libraries, one format. If this drifts, every login fails at once and it
    looks like a password problem, not a code problem.
    """
    password = "kitchen-password-123"
    stored = hash_password(password)

    # Lift verifyPassword out of the Worker rather than reimplement it here --
    # testing a copy proves nothing.
    worker = (Path(__file__).resolve().parents[1] / "worker" / "index.js").read_text()
    start = worker.index("async function verifyPassword")
    end = worker.index("// ── tokens")
    fn = worker[start:end]

    script = tmp_path / "t.mjs"
    script.write_text(
        "const enc = new TextEncoder();\n"
        "function timingSafeEqual(a,b){if(a.length!==b.length)return false;"
        "let o=0;for(let i=0;i<a.length;i++)o|=a[i]^b[i];return o===0;}\n"
        + fn +
        f"const ok = await verifyPassword({json.dumps(password)}, {json.dumps(stored)});\n"
        f"const bad = await verifyPassword('wrong-password', {json.dumps(stored)});\n"
        "console.log(JSON.stringify({ok, bad}));\n"
    )
    out = subprocess.run([shutil.which("node"), str(script)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["ok"] is True, "Worker could not verify a hash Python made — the formats have drifted"
    assert r["bad"] is False, "Worker accepted a wrong password"


@pytest.mark.xfail(
    reason="MIGRATION IN PROGRESS. dashboard/users.json is what the LIVE dashboard "
           "reads right now — deleting it blanks app.stowawaybar.com. It goes when "
           "index.html moves to the Worker. This test flips to pass on that day, "
           "which is the point of leaving it here rather than deleting it.",
    strict=False,
)
def test_old_scheme_is_gone():
    """
    The end state, asserted early.

    The old dashboard/users.json ships ONE global salt plus every hash to the
    browser and verifies in JS. Identity belongs in data/people.json (public,
    no secrets); hashes belong in the Worker and nowhere else.

    Old passwords cannot be migrated -- they are sha256(shared_salt + password),
    so there is nothing to convert. Everyone gets a new password when they get
    a personal account, which was going to happen anyway.
    """
    root = Path(__file__).resolve().parents[3]
    old = root / "dashboard" / "users.json"
    if old.exists():
        d = json.loads(old.read_text())
        assert "salt" not in d, (
            "dashboard/users.json still ships a global salt to the browser."
        )


def test_people_json_never_contains_a_secret():
    """
    THE INVARIANT THAT REPLACES IT. data/people.json is served to the browser.
    If a hash, salt or password ever lands in it, we have rebuilt the old bug
    with new filenames.
    """
    root = Path(__file__).resolve().parents[3]
    p = root / "data" / "people.json"
    if not p.exists():
        pytest.skip("no people yet — add one with modules.auth.cli")
    raw = p.read_text().lower()
    for forbidden in ("hash", "salt", "password", "pbkdf2", "secret", "token"):
        assert forbidden not in raw, (
            f"data/people.json contains {forbidden!r} and is PUBLIC — it is served to "
            f"every browser. Secrets belong in .secrets/passwords.json (gitignored) "
            f"and from there only into the Worker."
        )
