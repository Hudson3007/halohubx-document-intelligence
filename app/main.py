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

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session

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
from app.review import save_original_pdf

app = FastAPI(title="HaloHubX Document Intelligence API", version="0.1.0")
app.include_router(hitl.router)
app.include_router(review.router)
app.include_router(usage.router)
app.include_router(auth_console.router)
app.include_router(audit.router)


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

    try:
        ai_client = get_default_client()
        result = extract_document(ai_client, file_bytes, "application/pdf")
        doc.result_json = result
        doc.status = "completed"
        doc.completed_at = datetime.utcnow()
    except Exception as e:
        doc.status = "failed"
        doc.error_message = str(e)

    db.commit()
    db.refresh(doc)

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
    doc = db.query(Document).filter(Document.id == document_id, Document.partner_id == actor.partner_id).first()
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
    }


@app.get("/retrieve/{document_id}")
def retrieve_result(
    document_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(Document.id == document_id, Document.partner_id == actor.partner_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status != "completed":
        raise HTTPException(status_code=409, detail=f"Document is not ready yet (status: {doc.status})")
    return {
        "document_id": doc.id,
        "filename": doc.filename,
        "result": doc.result_json,
    }
