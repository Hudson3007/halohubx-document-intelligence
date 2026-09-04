"""
Phase 3 (partial): Human-in-the-loop (HITL) staging endpoints.

The team's Phase 0/1/2 backend exposes /upload, /status, /retrieve. This module
layers a small HITL slice on top so the React staging UI can:
  - list a partner's documents for review
  - pull the raw result + client for the side-by-side view
  - save a human-corrected result and re-deliver the webhook

Auth reuses the same `Authorization: Bearer <api_key>` scheme as the rest of the
API (see app/auth.py). It deliberately does NOT invent billing/metering or
low-confidence logic — that stays in the UI for now.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_actor, Actor
from app.db import get_db
from app.models import Client, Document
from app.webhooks import deliver_webhook

router = APIRouter(prefix="/hitl", tags=["hitl"])


@router.get("/documents")
def list_documents(
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """Recent documents for this partner, newest first (for the staging UI)."""
    docs = (
        db.query(Document)
        .filter(Document.partner_id == actor.partner_id)
        .order_by(Document.created_at.desc())
        .limit(50)
        .all()
    )
    return {
        "documents": [
            {
                "id": d.id,
                "client_name": _client_name(db, actor.partner_id, d.client_id),
                "filename": d.filename,
                "status": d.status,
                "webhook_delivered": d.webhook_delivered,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]
    }


@router.get("/document/{document_id}")
def get_document(
    document_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """Full result for the side-by-side review view (raw result + client)."""
    doc = (
        db.query(Document)
        .filter(Document.id == document_id, Document.partner_id == actor.partner_id)
        .first()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "document_id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
        "client_name": _client_name(db, actor.partner_id, doc.client_id),
        "result": doc.result_json,
        "error": doc.error_message,
    }


class ConfirmRequest(BaseModel):
    result: dict  # the human-corrected extraction result (multi-invoice JSON)


@router.post("/document/{document_id}/confirm")
def confirm_document(
    document_id: str,
    payload: ConfirmRequest,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """A clerk has validated/edited the data. Save the corrected result and
    deliver the webhook (if a URL is configured)."""
    doc = (
        db.query(Document)
        .filter(Document.id == document_id, Document.partner_id == actor.partner_id)
        .first()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.result_json = payload.result
    doc.status = "completed"
    doc.completed_at = datetime.utcnow()

    # Audit: record the human confirm/edit.
    from app.audit import record_from_actor
    n_inv = len((payload.result or {}).get("invoices") or [])
    record_from_actor(
        db, actor=actor, action="review_edit", document_id=doc.id,
        summary=f"Confirmed/corrected result ({n_inv} invoice{'s' if n_inv != 1 else ''})",
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


def _client_name(db: Session, partner_id: str, client_id: str) -> str | None:
    client = (
        db.query(Client)
        .filter(Client.id == client_id, Client.partner_id == partner_id)
        .first()
    )
    return client.name if client else None
