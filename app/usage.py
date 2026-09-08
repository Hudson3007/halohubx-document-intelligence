"""
Usage / credit metering endpoints.

  GET /usage  — a partner's credit situation for the current period:
                monthly quota, top-up balance, period usage, remainder.
                Used by the partner console dashboard's credit meter.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_role, get_actor, Actor
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


@router.get("/usage/ai")
def get_ai_usage(
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """Daily AI-request meter (free-tier Gemini ~20/day). Powers the console
    hint and lets uploads fail fast instead of silently failing extraction."""
    from app.ai_client import get_default_client
    from app.quota import used_today, daily_limit

    provider = get_default_client().provider
    used = used_today(db, provider)
    limit = daily_limit(provider)
    return {
        "provider": provider,
        "used_today": used,
        "daily_limit": limit,
        "remaining": max(0, limit - used),
    }


@router.post("/usage/ai/reset")
def reset_ai_usage(
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Owner-only: zero today's AI meter. Use when the real provider ceiling
    (Google AI Studio dashboard) disagrees with our counter — e.g. after a
    lossy stretch where throttled attempts were miscounted as consumed. The
    meter is a fail-fast convenience, not the source of truth: Google's
    dashboard is. Resetting lets uploads proceed until Google actually
    rejects, at which point mark_exhausted pins it at the limit again."""
    from app.ai_client import get_default_client
    from app.quota import _day_utc, daily_limit, used_today

    provider = get_default_client().provider
    day = _day_utc()
    from app.models import AiDailyUsage

    row = (
        db.query(AiDailyUsage)
        .filter(AiDailyUsage.provider == provider, AiDailyUsage.day == day)
        .first()
    )
    if row is not None:
        row.requests = 0
        db.commit()
    return {
        "provider": provider,
        "reset_day": day,
        "used_today": 0,
        "daily_limit": daily_limit(provider),
        "remaining": daily_limit(provider),
    }