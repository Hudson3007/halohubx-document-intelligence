"""
HITL Review Queue API — the endpoints the Streamlit "Review Queue" page
(and other clients) expect, layered on top of the core document model.

  GET  /documents?review_status=needs_review   -> list for review
  GET  /documents/{doc_id}/review              -> full detail + original PDF preview flag
  GET  /documents/{doc_id}/file                -> the original uploaded PDF
  POST /documents/{doc_id}/approve             -> save human-corrected result + webhook

The original PDF is persisted under UPLOAD_DIR/<document_id>.pdf so the
review UI can show the source next to the extracted data (see app/config.py
for the retained-files note). 
"""

import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_actor, Actor
from app.config import LOW_CONFIDENCE_THRESHOLD, UPLOAD_DIR
from app.db import get_db
from app.models import Client, Document
from app.webhooks import deliver_webhook

router = APIRouter(prefix="/documents", tags=["review"])

UPLOAD_PATH = Path(UPLOAD_DIR)
UPLOAD_PATH.mkdir(parents=True, exist_ok=True)


def pdf_path(document_id: str) -> Path:
    return UPLOAD_PATH / f"{document_id}.pdf"


def save_original_pdf(document_id: str, file_bytes: bytes) -> None:
    pdf_path(document_id).write_bytes(file_bytes)


def has_original_pdf(document_id: str) -> bool:
    return pdf_path(document_id).exists()


def normalize_result(result: dict) -> dict:
    """Return the result with a guaranteed top-level 'invoices' list.

    New documents store the multi-invoice shape ({invoice_count, invoices});
    older documents stored a single flat invoice at the top level. This
    normalises both so review UIs can always iterate result['invoices'].
    """
    result = result or {}
    if "invoices" in result and isinstance(result.get("invoices"), list):
        return result
    if any(k in result for k in ("vendor", "invoice_details", "total_amount")):
        return {
            "invoice_count": 1,
            "invoices": [result],
            "document_flags": result.get("document_flags") or result.get("flags") or [],
        }
    return {"invoice_count": 0, "invoices": [], "document_flags": []}


def invoice_count(result: dict) -> int:
    norm = normalize_result(result)
    return norm.get("invoice_count") or len(norm.get("invoices") or [])


def has_low_confidence(result: dict, threshold: float) -> bool:
    """True if any field across invoices is below the confidence threshold."""
    if not result:
        return False
    for inv in normalize_result(result).get("invoices", []):
        if (inv.get("vendor", {}).get("confidence", 1) or 1) < threshold:
            return True
        if (inv.get("invoice_details", {}).get("confidence", 1) or 1) < threshold:
            return True
        if (inv.get("total_amount", {}).get("confidence", 1) or 1) < threshold:
            return True
        for li in inv.get("line_items", []):
            if (li.get("confidence", 1) or 1) < threshold:
                return True
    return False


def _client_name(db: Session, partner_id: str, client_id: str) -> str:
    c = (
        db.query(Client)
        .filter(Client.id == client_id, Client.partner_id == partner_id)
        .first()
    )
    return c.name if c else None


def _get_partner_doc(db: Session, partner_id: str, document_id: str) -> Document:
    doc = db.query(Document).filter(Document.id == document_id, Document.partner_id == partner_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("")
def list_documents(
    review_status: str = "needs_review",
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """List this partner's documents. With review_status=needs_review (the
    default) it returns documents whose extraction has at least one
    low-confidence field, i.e. the HITL queue. Pass review_status=all to
    get every document, newest first."""
    docs = (
        db.query(Document)
        .filter(Document.partner_id == actor.partner_id)
        .order_by(Document.created_at.desc())
        .limit(200)
        .all()
    )
    items = []
    for d in docs:
        result = d.result_json if d.result_json and isinstance(d.result_json, dict) else {}
        needs = has_low_confidence(result, LOW_CONFIDENCE_THRESHOLD)
        if review_status == "all" or (review_status == "needs_review" and needs):
            items.append(
                {
                    "document_id": d.id,
                    "filename": d.filename,
                    "invoice_count": invoice_count(result),
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                    "status": d.status,
                    "needs_review": needs,
                }
            )
    return items


@router.get("/{document_id}/review")
def get_document_review(
    document_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """Full detail for the review screen: the normalised result, the source
    PDF flag, and the confidence threshold the review UI should highlight at."""
    doc = _get_partner_doc(db, actor.partner_id, document_id)
    result = doc.result_json if isinstance(doc.result_json, dict) else {}
    return {
        "document_id": doc.id,
        "filename": doc.filename,
        "client_name": _client_name(db, actor.partner_id, doc.client_id),
        "status": doc.status,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "low_confidence_threshold": LOW_CONFIDENCE_THRESHOLD,
        "has_file": has_original_pdf(doc.id),
        "result": normalize_result(result),
    }


@router.get("/{document_id}/file")
def get_document_file(
    document_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """Return the original uploaded PDF for side-by-side review preview."""
    _get_partner_doc(db, actor.partner_id, document_id)
    path = pdf_path(document_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="No original file stored for this document")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{document_id}.pdf",
    )


class ApproveRequest(BaseModel):
    result: dict


@router.post("/{document_id}/approve")
def approve_document(
    document_id: str,
    payload: ApproveRequest,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """A human corrected the extraction. Save it, mark completed, and deliver
    the webhook (if a URL is configured). Returns the delivery outcome."""
    doc = _get_partner_doc(db, actor.partner_id, document_id)

    doc.result_json = payload.result
    doc.status = "completed"
    doc.completed_at = datetime.utcnow()

    # Audit: record the approval and a brief summary of the reviewed shape.
    from app.audit import record_from_actor
    n_inv = invoice_count(payload.result)
    record_from_actor(
        db, actor=actor, action="approval", document_id=doc.id,
        summary=f"Approved result ({n_inv} invoice{'s' if n_inv != 1 else ''})",
    )
    db.commit()

    delivered = False
    if doc.webhook_url:
        delivered = deliver_webhook(
            doc.webhook_url,
            {
                "document_id": doc.id,
                "client_name": _client_name(db, actor.partner_id, doc.client_id),
                "status": doc.status,
                "result": doc.result_json,
            },
        )
        doc.webhook_delivered = delivered
        db.commit()

    return {
        "document_id": doc.id,
        "status": doc.status,
        "client_name": _client_name(db, actor.partner_id, doc.client_id),
        "webhook_delivered": delivered,
    }
