"""
Original-document storage backend.

Supports two backends, selected via UPLOAD_STORAGE:
  * "disk"     - write PDFs under UPLOAD_DIR (the default; suits a durable
                 filesystem or local dev).
  * "supabase" - write PDFs to a Supabase Storage bucket via its REST API,
                 so they survive episodic hosts (Render free tier wipes the
                 local disk on restart/sleep/redeploy).

Using the raw Storage REST API (httpx, already a dependency) avoids adding
the heavier `supabase` client just for file blobs.
"""

import io

import httpx

from app.config import (
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
    UPLOAD_BUCKET,
    UPLOAD_DIR,
    UPLOAD_STORAGE,
)

_STORAGE_BASE = f"{SUPABASE_URL}/storage/v1/object"


def _is_supabase() -> bool:
    return UPLOAD_STORAGE == "supabase" and bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"}


def write_original_pdf(document_id: str, file_bytes: bytes) -> None:
    if _is_supabase():
        url = f"{_STORAGE_BASE}/{UPLOAD_BUCKET}/{document_id}.pdf"
        resp = httpx.post(
            url,
            content=file_bytes,
            headers={
                **_auth_headers(),
                "Content-Type": "application/pdf",
                "x-upsert": "true",
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        return
    import os
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(os.path.join(UPLOAD_DIR, f"{document_id}.pdf"), "wb") as fh:
        fh.write(file_bytes)


def read_original_pdf(document_id: str) -> bytes:
    """Return the original PDF bytes, or None if not stored/accessible."""
    if _is_supabase():
        url = f"{_STORAGE_BASE}/{UPLOAD_BUCKET}/{document_id}.pdf"
        try:
            resp = httpx.get(url, headers=_auth_headers(), timeout=60.0)
        except httpx.HTTPError:
            return None
        if resp.status_code == 200:
            return resp.content
        return None
    import os
    path = os.path.join(UPLOAD_DIR, f"{document_id}.pdf")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def has_original_pdf(document_id: str) -> bool:
    if _is_supabase():
        url = f"{_STORAGE_BASE}/info/{UPLOAD_BUCKET}/{document_id}.pdf"
        try:
            resp = httpx.head(url, headers=_auth_headers(), timeout=30.0)
        except httpx.HTTPError:
            return False
        return resp.status_code == 200
    import os
    return os.path.exists(os.path.join(UPLOAD_DIR, f"{document_id}.pdf"))


def open_original_pdf(document_id: str) -> "io.BytesIO":
    """Return a file-like object for the stored PDF (thrown by the disk
    backend, created on the fly for the remote backend)."""
    data = read_original_pdf(document_id)
    if data is None:
        raise FileNotFoundError(document_id)
    return io.BytesIO(data)


def delete_original_pdf(document_id: str) -> None:
    if _is_supabase():
        url = f"{_STORAGE_BASE}/{UPLOAD_BUCKET}/{document_id}.pdf"
        try:
            httpx.delete(url, headers=_auth_headers(), timeout=30.0)
        except httpx.HTTPError:
            pass
        return
    import os
    path = os.path.join(UPLOAD_DIR, f"{document_id}.pdf")
    try:
        os.remove(path)
    except OSError:
        pass
