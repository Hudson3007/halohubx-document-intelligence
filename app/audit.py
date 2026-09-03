"""
Audit logging for console + machine actions.

Every review approval, document upload, and user login is recorded so the
owner can see who did what and when. The record() helper is called from the
modules that perform the action; the API here exposes it to the console.

  GET /audit?limit=50        — this partner's audit trail, newest first

The read endpoint accepts either a machine API key or a console session
token (see app.auth.get_actor).
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_actor, Actor
from app.db import get_db
from app.models import AuditLogEntry

router = APIRouter(prefix="/audit", tags=["audit"])


def record(
    db: Session,
    *,
    partner_id: str,
    action: str,
    actor_email: str | None = None,
    actor_role: str = "system",
    document_id: str | None = None,
    summary: str | None = None,
) -> AuditLogEntry:
    entry = AuditLogEntry(
        partner_id=partner_id,
        action=action,
        actor_email=actor_email,
        actor_role=actor_role,
        document_id=document_id,
        summary=summary,
    )
    db.add(entry)
    return entry  # caller commits


def record_from_actor(
    db: Session,
    *,
    actor: Actor,
    action: str,
    document_id: str | None = None,
    summary: str | None = None,
) -> AuditLogEntry:
    if actor.source == "session":
        role = actor.role
        email = actor.email
    else:
        role = "api_key"
        email = actor.email  # partner name for machine callers
    return record(
        db,
        partner_id=actor.partner_id,
        action=action,
        actor_email=email,
        actor_role=role,
        document_id=document_id,
        summary=summary,
    )


@router.get("")
def list_audit(
    limit: int = 50,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    query = (
        db.query(AuditLogEntry)
        .filter(AuditLogEntry.partner_id == actor.partner_id)
        .order_by(AuditLogEntry.created_at.desc())
        .limit(min(limit, 200))
    )
    return {
        "entries": [
            {
                "id": e.id,
                "action": e.action,
                "actor_email": e.actor_email,
                "actor_role": e.actor_role,
                "document_id": e.document_id,
                "summary": e.summary,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in query.all()
        ]
    }