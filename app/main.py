"""
HaloHubX Document Intelligence API — Phase 0/1/2.

Endpoints:
  POST /upload    — submit a PDF for extraction (sync processing for MVP)
  GET  /status/{document_id}
  GET  /retrieve/{document_id}

Auth: Authorization: Bearer <partner_api_key>  (see app/auth.py)

Run locally:
    uvicorn app.main:app --reload

Before first run, set DATABASE_URL + AI provider key in .env, then:
    python -m app.init_db
    python create_partner.py "Vamsi Integrations"   # prints an api_key
"""

from datetime import datetime

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.logging_setup import setup_logging, get_logger, elapsed_ms
setup_logging()
log = get_logger("main")

from app.db import get_db
from app.models import Partner, Client, Document, User
from app.auth import get_actor, Actor, require_role
from app.config import MAX_UPLOAD_BYTES
from app.extraction import extract_document
from app.ai_client import get_default_client
from app.billing import availability, charge_pages
from app.pages import count_pdf_pages
from app.webhooks import deliver_webhook
from app import hitl, review, usage, auth_console, audit
from app import payments as payments_mod
from app import search as search_mod
from app import batch as batch_mod
from app.review import save_original_pdf
from app import health as health_mod

app = FastAPI(title="HaloHubX Document Intelligence API", version="0.1.0")
app.include_router(hitl.router)
app.include_router(review.router)
app.include_router(usage.router)
app.include_router(auth_console.router)
app.include_router(audit.router)
app.include_router(health_mod.router)
app.include_router(payments_mod.router)
app.include_router(search_mod.router)
app.include_router(batch_mod.router)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    """Emit one structured JSON log line per request, and feed /metrics."""
    import time as _t
    start = _t.monotonic()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        status_code = 500
        log.exception("unhandled request error", extra={"path": request.url.path})
        raise
    finally:
        dur = round((_t.monotonic() - start) * 1000, 1)
        health_mod.record_request(
            request.method, request.url.path, status_code, dur,
            provider=getattr(request.state, "ai_provider", ""),
        )
        log.info("request",
                 extra={"method": request.method,
                        "path": request.url.path,
                        "status": status_code,
                        "duration_ms": dur})


def _get_or_create_client(db: Session, partner: Partner, client_name: str) -> Client:
    client = (
        db.query(Client)
        .filter(Client.partner_id == partner.id, Client.name == client_name)
        .first()
    )
    if client is None:
        client = Client(partner_id=partner.id, name=client_name)
        db.add(client)
        db.commit()
        db.refresh(client)
    return client


@app.get("/")
def root():
    return {"service": "HaloHubX Document Intelligence API", "status": "ok"}


@app.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    client_name: str = Form(...),
    webhook_url: str = Form(None),
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Submit a PDF for extraction. Processes synchronously (MVP) and
    returns the result directly, AND delivers it to webhook_url (or the
    partner's default_webhook_url) if one is set — so partners can use
    either the response body or the webhook, whichever fits their ERP.

    The original PDF is stored under UPLOAD_DIR so the HITL Review Queue
    (see /documents/...) can show a side-by-side source preview."""

    partner = db.query(Partner).filter(Partner.id == actor.partner_id).first()
    if partner is None:
        raise HTTPException(status_code=401, detail="Partner account not found.")

    if file.content_type != "application/pdf" and not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    file_bytes = await file.read()

    # --- Upload safety: size cap + strict PDF validation ---
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
        )
    if len(file_bytes) < 10:
        raise HTTPException(status_code=400, detail="File is too small to be a valid PDF.")

    # Strict PDF validation: check magic bytes AND attempt a real parse with
    # PyMuPDF. This rejects non-PDF files (even if renamed to .pdf), corrupt
    # PDFs, and polyglot attacks (e.g. a ZIP renamed to .pdf).
    if not file_bytes.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="File does not appear to be a valid PDF (missing PDF header).")
    try:
        import fitz
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            if doc.page_count < 1:
                raise ValueError("PDF has no pages")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="File could not be parsed as a valid PDF.")

    # --- Credit metering: count pages, guarantee budget, charge up-front ---
    # 1 credit = 1 PDF page. We compute pages before creating the Document so
    # we can reject with 402 when the partner is out of credits, and we charge
    # as soon as extraction starts (pages were consumed regardless of outcome).
    page_count = count_pdf_pages(file_bytes)
    budget = availability(db, partner, now=datetime.utcnow())
    if page_count > budget.remaining_total:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "insufficient_credits",
                "message": (
                    "Not enough credits for this document. "
                    f"Document needs {page_count} page(s); you have "
                    f"{budget.remaining_total} credit(s) ({budget.to_dict()})."
                ),
                "required_pages": page_count,
                "available": budget.remaining_total,
                "usage": budget.to_dict(),
            },
        )

    client_row = _get_or_create_client(db, partner, client_name)

    # --- Daily AI quota guard: fail fast BEFORE charging credits ---
    # Free-tier Gemini caps at ~20 requests/day. Refusing the upload here (429)
    # beats charging pages and then failing extraction minutes later.
    from app.quota import check_quota
    provider = get_default_client().provider
    check_quota(db, provider, needed=1)

    doc = Document(
        partner_id=partner.id,
        client_id=client_row.id,
        filename=file.filename,
        status="processing",
        page_count=page_count,
        webhook_url=webhook_url or partner.default_webhook_url,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Charge for the pages now that extraction is about to run.
    charge_pages(db, partner, page_count, now=datetime.utcnow())

    ai_client = None
    try:
        ai_client = get_default_client()
        from app.quota import record_request
        record_request(db, ai_client.provider, 1)
        result = extract_document(ai_client, file_bytes, "application/pdf")
        doc.result_json = result
        doc.status = "completed"
        doc.completed_at = datetime.utcnow()
    except Exception as e:
        doc.status = "failed"
        from app.ai_client import _summarize_error
        doc.error_message = _summarize_error(e)

    db.commit()
    db.refresh(doc)

    health_mod.record_document(ok=(doc.status == "completed"))
    provider = getattr(ai_client, "provider", "unknown")
    request.state.ai_provider = provider or "unknown"
    log.info("document_processed",
             extra={"document_id": doc.id, "partner_id": partner.id,
                    "client_name": client_row.name, "pages": page_count,
                    "status": doc.status, "ai_provider": provider})

    # Store the original file so the HITL / Review Queue UI can show a
    # side-by-side preview while a human corrects the extraction. (This is a
    # deliberate shift away from the earlier zero-retention stance — the
    # source is now kept under UPLOAD_DIR so review is possible.)
    save_original_pdf(doc.id, file_bytes)

    # Audit: record who submitted the document.
    from app.audit import record_from_actor
    record_from_actor(
        db, actor=actor, action="upload", document_id=doc.id,
        summary=f"Uploaded {file.filename} ({page_count} page{'s' if page_count != 1 else ''})",
    )
    db.commit()

    if doc.webhook_url and doc.status == "completed":
        delivered = deliver_webhook(
            doc.webhook_url,
            {
                "document_id": doc.id,
                "client_name": client_row.name,
                "status": doc.status,
                "result": doc.result_json,
            },
        )
        doc.webhook_delivered = delivered
        db.commit()
        health_mod.record_webhook(ok=delivered)

    return {
        "document_id": doc.id,
        "status": doc.status,
        "result": doc.result_json,
        "error": doc.error_message,
    }


@app.get("/status/{document_id}")
def get_status(
    document_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(Document.id == document_id, Document.partner_id == actor.partner_id, Document.deleted_at.is_(None)).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "document_id": doc.id,
        "status": doc.status,
        "filename": doc.filename,
        "page_count": doc.page_count,
        "created_at": doc.created_at,
        "completed_at": doc.completed_at,
        "webhook_delivered": doc.webhook_delivered,
        "error": doc.error_message,
    }


@app.get("/retrieve/{document_id}")
def retrieve_result(
    document_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(Document.id == document_id, Document.partner_id == actor.partner_id, Document.deleted_at.is_(None)).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status != "completed":
        raise HTTPException(status_code=409, detail=f"Document is not ready yet (status: {doc.status})")
    return {
        "document_id": doc.id,
        "filename": doc.filename,
        "result": doc.result_json,
    }
