"""
Daily AI-request quota guard.

Free-tier Gemini caps at ~20 requests/day; every document extraction is one
request. We record each extraction attempt against a per-provider, per-UTC-day
counter so:

  * uploads fail fast when the budget is gone (no silent charge + failed doc),
  * the console can show "X / N AI requests used today",
  * batch/background workers don't hammer a provider that will reject them.

Not partner-scoped: the (current) default AI client is shared server-side, so
a single counter per (provider, day) is the honest budget.
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import AI_DAILY_LIMITS
from app.models import AiDailyUsage

# Google resets RPD ("requests per day") at MIDNIGHT PACIFIC time, not UTC.
# Storing the day in a fixed point-in-time zone keeps our meter aligned with
# what Google actually counts, so the badge and fail-fast don't drift by the
# UTC-vs-PT offset (that discrepancy is exactly what left the meter showing
# 20/20 while Google only counted 15/20 today).
_DAY_TZ = timezone.utc  # kept UTC for UTC-locale deployments; override below
try:
    from zoneinfo import ZoneInfo

    _DAY_TZ = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover - zoneinfo always available on py3.9+
    pass


def _day_utc() -> str:
    # Legacy name kept for import compatibility; now Pacific-window keyed.
    return datetime.now(_DAY_TZ).strftime("%Y-%m-%d")


def daily_limit(provider: str) -> int:
    return AI_DAILY_LIMITS.get(provider, AI_DAILY_LIMITS.get("gemini", 20))


def used_today(db: Session, provider: str) -> int:
    row = (
        db.query(AiDailyUsage)
        .filter(AiDailyUsage.provider == provider, AiDailyUsage.day == _day_utc())
        .first()
    )
    return row.requests if row else 0


def remaining_today(db: Session, provider: str) -> int:
    return max(0, daily_limit(provider) - used_today(db, provider))


def record_request(db: Session, provider: str, n: int = 1) -> None:
    """Add n requests to today's counter for the provider (idempotent-ish
    upsert). Call right before the provider call; the attempt is what counts
    regardless of whether the call succeeds."""
    day = _day_utc()
    row = (
        db.query(AiDailyUsage)
        .filter(AiDailyUsage.provider == provider, AiDailyUsage.day == day)
        .first()
    )
    if row is None:
        row = AiDailyUsage(provider=provider, day=day, requests=0)
        db.add(row)
    row.requests += n
    db.flush()


def mark_exhausted(db: Session, provider: str) -> None:
    """Set today's counter to the daily limit. Call when the provider itself
    reports the per-day cap is hit, so the badge stops claiming remaining
    requests that Google will reject anyway (Google counts retry attempts the
    local meter can't see ahead of time)."""
    day = _day_utc()
    row = (
        db.query(AiDailyUsage)
        .filter(AiDailyUsage.provider == provider, AiDailyUsage.day == day)
        .first()
    )
    limit = daily_limit(provider)
    if row is None:
        row = AiDailyUsage(provider=provider, day=day, requests=limit)
        db.add(row)
    elif row.requests < limit:
        row.requests = limit
    db.flush()


def check_quota(db: Session, provider: str, needed: int = 1) -> None:
    """Raise HTTP 429 (with a clear message) when fewer than `needed`
    requests remain today. Call before charging credits / spawning work."""
    remaining = remaining_today(db, provider)
    if remaining < needed:
        limit = daily_limit(provider)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "ai_quota_exhausted",
                "message": (
                    f"Daily AI request quota reached ({remaining}/{limit} remaining). "
                    "Extraction resumes after midnight Pacific time (Google's "
                    "reset window) — re-upload then, or upgrade the provider "
                    "API key for a higher daily limit."
                ),
                "used": limit - remaining,
                "limit": limit,
                "provider": provider,
            },
        )