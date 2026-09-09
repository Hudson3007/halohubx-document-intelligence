#!/usr/bin/env python3
"""
Purge "dead" documents — failed documents whose original PDF no longer
exists anywhere (Supabase bucket / disk), so extraction can NEVER be
retried. They just clutter the review queue, the failed list and the
auto-retry scan forever, so this deletes them permanently.

What counts as dead:
  * status == "failed"
  * not soft-deleted (still visible in the console)
  * no stored original PDF (has_original_pdf() resolves to False)
    -> never retryable: the model can't re-read a file that's gone.

Everything else is left alone (completed docs, soft-deleted docs awaiting
the bin flow, failed docs whose PDF still exists and can be retried).

Safe by design:
  * dry-run by default; pass --execute to actually delete rows.
  * prints a per-doc summary + audit trail entry before removal.

Run with the backend venv, example:
    python scripts/purge_dead_docs.py            # show what would be removed
    python scripts/purge_dead_docs.py --execute  # actually remove them

Requires a working DATABASE_URL (see .env). The storage check talks to the
Supabase Storage REST API for the public URL check only — no service-role
key is needed for existence checks on a public bucket.
"""

import argparse
import ipaddress
import os
import sys
from urllib.parse import urlsplit

# The DB layer refuses a `postgres` superuser connection by default; the
# project's .env uses exactly that for its Supabase URL. An operator running
# this maintenance script explicitly accepts that (same as init_db etc.).
os.environ.setdefault("ALLOW_SUPERUSER_DB", "1")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.config import SUPABASE_URL, UPLOAD_BUCKET, UPLOAD_STORAGE  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import AuditLogEntry, Document  # noqa: E402
from app.webhooks import deliver_webhook  # noqa: F401  (imports ssrf guard etc.)  pylint: disable=unused-import


def _infer_supabase_url() -> str:
    """Derive https://<ref>.supabase.co from the DATABASE_URL host.

    Handles both host shapes Supabase uses:
      * db.<ref>.supabase.co
      * <ref>.supabase.co
    """
    db_url = os.environ.get("DATABASE_URL", "")
    host = urlsplit(db_url).hostname or ""
    host = host.rstrip(".").lower()
    if host.endswith(".supabase.co"):
        parts = host.split(".")
        # "db.<ref>.supabase.co" -> ref is parts[1]; "<ref>.supabase.co" -> parts[0]
        ref = parts[1] if parts[0] == "db" and len(parts) >= 4 else parts[0]
        if ref:
            return f"https://{ref}.supabase.co"
    return ""


def _public_base() -> str:
    base = (SUPABASE_URL or _infer_supabase_url()).rstrip("/")
    return f"{base}/storage/v1/object/public/{UPLOAD_BUCKET}"


def _has_stored_pdf(document_id: str) -> bool:
    """True if the original PDF is retrievable from storage. Uses the public
    bucket URL so existence checks work without a service-role key."""
    try:
        r = httpx.head(f"{_public_base()}/{document_id}.pdf", timeout=30, follow_redirects=True)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def find_dead_docs(db):
    rows = (
        db.query(Document)
        .filter(
            Document.status == "failed",
            Document.deleted_at.is_(None),
        )
        .order_by(Document.created_at.desc())
        .all()
    )
    dead = [d for d in rows if not _has_stored_pdf(d.id)]
    return dead


def main() -> int:
    ap = argparse.ArgumentParser(description="Purge failed documents whose original PDF is gone.")
    ap.add_argument("--execute", action="store_true", help="Actually delete rows (default: dry-run).")
    args = ap.parse_args()

    print(f"[storage] backend={UPLOAD_STORAGE or 'disk'} bucket={UPLOAD_BUCKET} "
          f"public_base={_public_base()}")
    print(f"[storage] UPLOAD_STORAGE env={os.environ.get('UPLOAD_STORAGE')!r}")

    db = SessionLocal()
    try:
        dead = find_dead_docs(db)
        print(f"\nFound {len(dead)} dead document(s) (failed + no stored PDF):\n")
        for d in dead:
            print(f"  - {d.id}  {d.filename}  (created {d.created_at.isoformat() if d.created_at else '?'})")
            if d.error_message:
                print(f"      error: {d.error_message[:120]}")
        if not dead:
            print("Nothing to purge.")
            return 0

        if not args.execute:
            print("\nDry-run — nothing deleted. Re-run with --execute to purge.")
            return 0

        # Delete the doc rows + any orphaned audit entries pointing at them.
        for d in dead:
            from app.audit import record
            record(
                db,
                partner_id=d.partner_id,
                action="purge",
                actor_email="system",
                actor_role="system",
                document_id=d.id,
                summary=f"Purged dead document {d.filename} (failed, original PDF no longer stored)",
            )
            db.delete(d)
            # Keep the audit trail for this doc readable: drop old entries that
            # referenced the document (upload/delete/etc.) so nothing dangles.
            db.query(AuditLogEntry).filter(
                AuditLogEntry.document_id == d.id,
                AuditLogEntry.action != "purge",
            ).delete(synchronize_session=False)
        db.commit()
        print(f"\nPurged {len(dead)} document(s).")
        return 0
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())