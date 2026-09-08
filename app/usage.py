"""
Usage / credit metering endpoints.

  GET /usage  — a partner's credit situation for the current period:
                monthly quota, top-up balance, period usage, remainder.
                Used by the partner console dashboard's credit meter.
"""

from fastapi import APIRouter, Body, Depends, HTTPException
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
    used: int = Body(0, embed=True),
):
    """Owner-only: realign today's AI meter with the provider's actual ceiling
    as shown in Google AI Studio (aistudio.google.com/rate-limit). Google is
    the source of truth; this counter is just a fail-fast convenience. Pass
    used=<n> to calibrate to your dashboard (e.g. used=15 when RPD shows
    15/20), or used=0 to reset for a fresh window. On the next genuine
    provider rejection mark_exhausted pins it at the limit again."""
    from app.ai_client import get_default_client
    from app.quota import _day_utc, daily_limit, used_today

    provider = get_default_client().provider
    day = _day_utc()
    if used < 0 or used > daily_limit(provider):
        raise HTTPException(status_code=400, detail=f"used must be 0..{daily_limit(provider)}.")
    from app.models import AiDailyUsage

    row = (
        db.query(AiDailyUsage)
        .filter(AiDailyUsage.provider == provider, AiDailyUsage.day == day)
        .first()
    )
    if row is None:
        row = AiDailyUsage(provider=provider, day=day, requests=used)
        db.add(row)
    else:
        row.requests = used
    db.commit()
    return {
        "provider": provider,
        "reset_day": day,
        "used_today": used,
        "daily_limit": daily_limit(provider),
        "remaining": max(0, daily_limit(provider) - used),
    }