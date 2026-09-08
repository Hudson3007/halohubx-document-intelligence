"""
Bulk upload — accept many PDFs in a single request.

Unlike the synchronous /upload endpoint (one doc, returns after extraction),
this endpoint:
  1. validates every file (PDF magic bytes + PyMuPDF parse + size cap),
  2. checks the partner's credit budget against the TOTAL page count,
  3. creates a Document row per accepted file and charges exactly that,
  4. saves every original PDF to disk,
  5. returns immediately while a bounded thread pool extracts each document
     in the background (updating status as it goes).

This is what makes uploading hundreds or thousands of documents practical:
the HTTP request is only as long as the upload itself, and the AI work fans
out across workers instead of blocking the caller.

Endpoint:
    POST /upload/batch        multipart: files[], client_name, webhook_url?
    POST /status/batch        {"document_ids": [...]} -> per-doc status
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.ai_client import get_default_client
from app.auth import Actor, get_actor, require_role
from app.config import MAX_UPLOAD_BYTES
from app.db import SessionLocal, get_db
from app.extraction import extract_document
from app.logging_setup import get_logger
from app.models import Client, Document, Partner
from app.pages import count_pdf_pages

log = get_logger("batch")

router = APIRouter(tags=["batch"])

# Bounded concurrency for background extraction. A couple of workers keeps
# Gemini/Claude API rate limits (429s) happy while still fanning out large
# batches. Each worker is a separate API call; going too wide trips the
# provider's per-minute quota and marks documents 'failed' with 429s.
_WORKERS = 2
_executor = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="hhx-batch")


def _validate_pdf(filename: str, content_type: str | None, file_bytes: bytes) -> int:
    """Return page count, or raise HTTPException with a precise rejection reason."""
    if content_type != "application/pdf" and not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Not a PDF file.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
        )
    if len(file_bytes) < 10:
        raise HTTPException(status_code=400, detail="File is too small to be a valid PDF.")
    if not file_bytes.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="Missing PDF header (not a valid PDF).")
    try:
        import fitz
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            if doc.page_count < 1:
                raise ValueError("PDF has no pages")
            pages = doc.page_count
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Could not be parsed as a valid PDF.")
    return pages


def _requeue_stale_processing() -> None:
    """On startup, anything left 'processing' from a previous run was being
    extracted when the process died (Render restarts on deploy/sleep). Thanks
    to Supabase storage the original PDFs survive, so instead of failing them
    we re-queue them for extraction. Docs whose PDF truly is gone fail later
    in the worker with a clear 're-upload' message."""
    db = SessionLocal()
    try:
        stale = db.query(Document).filter(Document.status == "processing").all()
        for doc in stale:
            db.add(doc)
        if stale:
            db.commit()
            for doc in stale:
                db.refresh(doc)
            # Fresh thread launches so the current startup isn't blocked.
            for doc in stale:
                _executor.submit(_extract_in_background, doc.id)
            log.info("batch.requeued_stale", extra={"count": len(stale)})
    except Exception:
        db.rollback()
        log.exception("batch.requeue_stale_error")
    finally:
        db.close()


def _extract_in_background(document_id: str) -> None:
    """Worker task: run extraction for one stored document using its own DB
    session (the request session must not be touched from this thread)."""
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc is None or doc.status == "failed":
            return
        from app.storage import read_original_pdf
        file_bytes = read_original_pdf(document_id)
        if not file_bytes:
            # Transient hosts (Render free tier) wipe local storage on
            # restart/redeploy; a missing original means retry is impossible
            # without a fresh upload. Fail with a clear message rather than
            # passing empty bytes into the provider.
            doc.status = "failed"
            doc.error_message = (
                "Original PDF is no longer available on the server "
                "(storage was cleared on restart/redeploy). Re-upload the document to retry."
            )
            db.commit()
            log.info("batch.missing_original", extra={"document_id": document_id})
            return

        ai_client = get_default_client()
        try:
            from app.quota import record_request

            def _on_attempt():
                record_request(db, ai_client.provider, 1)

            result = extract_document(ai_client, file_bytes, "application/pdf", on_attempt=_on_attempt)
            doc.result_json = result
            doc.status = "completed"
            doc.completed_at = datetime.utcnow()
        except Exception as e:
            doc.status = "failed"
            from app.ai_client import _summarize_error
            doc.error_message = _summarize_error(e)
            from app.quota import mark_exhausted
            from app.ai_client import _is_daily_quota

            if _is_daily_quota(e):
                mark_exhausted(db, getattr(ai_client, "provider", "gemini"))
        db.commit()

        from app import health as health_mod
        health_mod.record_document(ok=(doc.status == "completed"))
        log.info(
            "batch.document_processed",
            extra={
                "document_id": document_id,
                "status": doc.status,
                "provider": getattr(ai_client, "provider", "unknown"),
            },
        )

        if doc.webhook_url and doc.status == "completed":
            from app.webhooks import deliver_webhook
            client_name = None
            if doc.client_id:
                c = db.query(Client).filter(Client.id == doc.client_id).first()
                client_name = c.name if c else None
            delivered = deliver_webhook(
                doc.webhook_url,
                {
                    "document_id": doc.id,
                    "client_name": client_name,
                    "status": doc.status,
                    "result": doc.result_json,
                },
            )
            doc.webhook_delivered = delivered
            db.commit()
            health_mod.record_webhook(ok=delivered)
    except Exception:
        db.rollback()
        log.exception("batch.extract_error", extra={"document_id": document_id})
    finally:
        db.close()


_requeue_stale_processing()


@router.post("/upload/batch")
async def upload_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    client_name: str = Form(...),
    webhook_url: str = Form(None),
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Accept many PDFs at once. Validates each file, charges credits for the
    combined page count, persists every document, then extracts them all in the
    background. Returns the accepted set (to poll with /status/batch) plus any
    individually-rejected files."""
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")

    partner = db.query(Partner).filter(Partner.id == actor.partner_id).first()
    if partner is None:
        raise HTTPException(status_code=401, detail="Partner account not found.")

    # 1 -- Validate every uploaded file individually so one bad PDF doesn't
    #      sink the whole batch; reject non-PDF/corrupt/oversized files.
    accepted = []   # (filename, page_count, bytes)
    rejected = []   # (filename, reason)
    for f in files:
        name = f.filename or "unnamed.pdf"
        try:
            data = await f.read()
            pages = _validate_pdf(name, f.content_type, data)
            accepted.append((name, pages, data))
        except HTTPException as e:
            rejected.append({"filename": name, "reason": str(e.detail)})

    if not accepted:
        raise HTTPException(
            status_code=400,
            detail={"error": "no_valid_files", "message": "None of the uploaded files were valid PDFs.", "rejected": rejected},
        )

    # 2 -- Credit check against the TOTAL page count up-front.
    total_pages = sum(p for _, p, _ in accepted)
    from app.billing import availability
    budget = availability(db, partner, now=datetime.utcnow())
    if total_pages > budget.remaining_total:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "insufficient_credits",
                "message": (
                    f"Batch needs {total_pages} credit(s) ({len(accepted)} file(s)); "
                    f"you have {budget.remaining_total} credit(s)."
                ),
                "required_pages": total_pages,
                "available": budget.remaining_total,
                "usage": budget.to_dict(),
            },
        )

    # 2b -- Daily AI request quota: fail fast BEFORE charging credits or
    #      spawning background work (free-tier Gemini caps at ~20 req/day).
    from app.quota import check_quota
    provider = get_default_client().provider
    check_quota(db, provider, needed=len(accepted))

    # 3 -- One client (created once per batch) + a Document row per file.
    client_row = (
        db.query(Client)
        .filter(Client.partner_id == partner.id, Client.name == client_name)
        .first()
    )
    if client_row is None:
        client_row = Client(partner_id=partner.id, name=client_name)
        db.add(client_row)
        db.commit()
        db.refresh(client_row)

    docs = []
    for name, pages, _data in accepted:
        doc = Document(
            partner_id=partner.id,
            client_id=client_row.id,
            filename=name,
            status="processing",
            page_count=pages,
            webhook_url=webhook_url or partner.default_webhook_url,
        )
        db.add(doc)
        docs.append((name, pages, doc))
    db.commit()
    for _name, _pages, doc in docs:
        db.refresh(doc)

    # 4 -- Charge at submission time (pages are consumed regardless of outcome).
    from app.billing import charge_pages
    charge_pages(db, partner, total_pages, now=datetime.utcnow())

    # 5 -- Persist the originals so the background workers can read them off disk.
    from app.review import save_original_pdf
    for (name, pages, doc), (_name, _pages, data) in zip(docs, accepted):
        save_original_pdf(doc.id, data)

    # 6 -- Audit the submission (one row per accepted file).
    from app.audit import record_from_actor
    for name, pages, doc in docs:
        record_from_actor(
            db,
            actor=actor,
            action="upload",
            document_id=doc.id,
            summary=f"Uploaded {name} ({pages} page{'s' if pages != 1 else ''}) [batch]",
        )
    db.commit()

    # 7 -- Fan out extraction to the background pool; return immediately.
    #      Stagger the launches slightly so concurrent workers don't hit the
    #      provider's rate limit simultaneously.
    doc_ids = [doc.id for _name, _pages, doc in docs]
    for i, doc_id in enumerate(doc_ids):
        delay = i * 1.0

        def _launch(doc_id=doc_id, delay=delay):
            if delay:
                time.sleep(delay)
            _executor.submit(_extract_in_background, doc_id)

        _launch()

    log.info(
        "batch.submitted",
        extra={
            "partner_id": partner.id,
            "files": len(docs),
            "pages": total_pages,
            "rejected": len(rejected),
        },
    )

    return {
        "submitted": len(docs),
        "rejected": rejected,
        "total_pages": total_pages,
        "documents": [
            {"document_id": doc.id, "filename": name, "status": "processing"}
            for name, _pages, doc in docs
        ],
    }


@router.post("/retry")
def retry_failed(
    payload: dict,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """Re-queue documents that failed extraction (e.g. transient provider
    rate-limit 429s). Accepts explicit ids, or 'all_failed' to retry every
    failed document for this partner."""
    ids = payload.get("document_ids") or payload.get("ids") or []
    all_failed = bool(payload.get("all_failed"))

    query = db.query(Document).filter(
        Document.partner_id == actor.partner_id,
        Document.status == "failed",
        Document.deleted_at.is_(None),
    )
    if all_failed:
        rows = query.all()
    else:
        rows = []
        for i in ids:
            doc = db.query(Document).filter(
                Document.id == i, Document.partner_id == actor.partner_id, Document.status == "failed"
            ).first()
            if doc:
                rows.append(doc)

    if not rows:
        return {"queued": 0, "documents": []}

    # Don't re-queue work the daily AI budget can't afford right now.
    from app.quota import check_quota
    provider = get_default_client().provider
    check_quota(db, provider, needed=len(rows))

    # Reset to processing so the queue stays consistent and credits already
    # charged are NOT charged again (pages were consumed at original submit).
    retried = []
    for doc in rows:
        if not (doc.status == "failed"):
            continue
        doc.status = "processing"
        doc.error_message = None
        doc.completed_at = None
        db.add(doc)
        retried.append(doc)
    db.commit()
    for doc in retried:
        db.refresh(doc)

    for i, doc in enumerate(retried):
        delay = i * 1.0

        def _launch(doc_id=doc.id, delay=delay):
            if delay:
                time.sleep(delay)
            _executor.submit(_extract_in_background, doc_id)

        _launch()

    from app.audit import record_from_actor
    record_from_actor(
        db,
        actor=actor,
        action="retry",
        summary=f"Re-queued {len(retried)} failed document(s) for extraction",
    )
    db.commit()

    return {
        "queued": len(retried),
        "documents": [
            {"document_id": doc.id, "filename": doc.filename, "status": "processing"}
            for doc in retried
        ],
    }


@router.post("/status/batch")
def batch_status(
    payload: dict,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """One bulk status call for many document ids — avoids polling /status/{id}
    once per document when a batch has hundreds or thousands of files."""
    ids = payload.get("document_ids") or []
    if not isinstance(ids, list) or not ids:
        return {"documents": []}
    if len(ids) > 5000:
        raise HTTPException(status_code=413, detail="Too many ids in one status request (max 5000).")

    rows = (
        db.query(Document)
        .filter(
            Document.id.in_(ids),
            Document.partner_id == actor.partner_id,
            Document.deleted_at.is_(None),
        )
        .all()
    )
    by_id = {d.id: d for d in rows}
    return {
        "documents": [
            {
                "document_id": doc.id,
                "filename": doc.filename,
                "status": doc.status,
                "page_count": doc.page_count,
                "error": doc.error_message,
            }
            for doc in (by_id.get(i) for i in ids)
            if doc is not None
        ]
    }