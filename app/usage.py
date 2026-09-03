"""
Usage / credit metering endpoints.

  GET /usage  — a partner's credit situation for the current period:
                monthly quota, top-up balance, period usage, remainder.
                Used by the partner console dashboard's credit meter.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_actor, Actor
from app.billing import availability
from app.db import get_db
from app.models import Partner

router = APIRouter()


@router.get("/usage")
def get_usage(
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    partner = db.query(Partner).filter(Partner.id == actor.partner_id).first()
    if partner is None:
        raise HTTPException(status_code=404, detail="Partner account not found.")
    avail = availability(db, partner)
    payload = avail.to_dict()
    payload["period"] = partner.current_month_ref or "unknown"
    payload["partner_name"] = partner.name
    return payload