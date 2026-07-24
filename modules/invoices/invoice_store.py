"""
Keep the original invoice PDF so a human can always open the source document
(Zak: "reference the actual invoice too just in case") — the way Dext shows the
scan next to the coding.

PDFs live in a PRIVATE Supabase Storage bucket ("invoices"), named by a hash of
their bytes (so the same file never uploads twice, and the name isn't guessable).
The Mac uploads with the service key; the app opens them with short-lived signed
URLs it requests as the signed-in admin. Nothing is public.

    pdf_key(bytes)                 -> "<sha256>.pdf"
    upload_pdf(bytes)              -> the key (uploads if new)  [Mac, service key]
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

SUPA_URL = "https://fyqhvyvwbedoowjkrxyj.supabase.co"
BUCKET = "invoices"
KEY_FILE = Path.home() / "Documents" / "STOW" / ".secrets" / "supabase_service_key"


def pdf_key(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest() + ".pdf"


def _svc_key() -> str:
    return KEY_FILE.read_text().strip()


def upload_pdf(pdf_bytes: bytes) -> str:
    """Upload to the private bucket if not already there; return the object key."""
    key = pdf_key(pdf_bytes)
    svc = _svc_key()
    req = urllib.request.Request(
        f"{SUPA_URL}/storage/v1/object/{BUCKET}/{key}",
        data=pdf_bytes, method="POST",
        headers={"apikey": svc, "authorization": f"Bearer {svc}",
                 "content-type": "application/pdf",
                 "x-upsert": "true"})        # idempotent — re-uploading the same file is fine
    try:
        urllib.request.urlopen(req).read()
    except urllib.error.HTTPError as e:
        if e.code not in (200, 409):          # 409 = already exists
            raise
    return key


def ensure_bucket() -> None:
    """Create the private bucket once (idempotent)."""
    svc = _svc_key()
    import json
    req = urllib.request.Request(
        f"{SUPA_URL}/storage/v1/bucket",
        data=json.dumps({"id": BUCKET, "name": BUCKET, "public": False}).encode(),
        method="POST",
        headers={"apikey": svc, "authorization": f"Bearer {svc}", "content-type": "application/json"})
    try:
        urllib.request.urlopen(req).read()
    except urllib.error.HTTPError as e:
        if e.code not in (200, 409):
            raise
