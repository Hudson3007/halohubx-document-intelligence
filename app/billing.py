"""
Usage / credit metering.

Pricing model: 1 credit = 1 PDF page scanned.

A partner has two credit sources:
  * monthly_credit_quota — granted at the start of every period (resets)
  * topup_credits        — one-time purchased/add-on pool (never resets)

Top-ups are consumed first, then the monthly quota. Extraction proceeds
while `topup_credits + (monthly_credit_quota - current_month_used) > 0`.

The current period is tracked by `current_month_ref` (a "YYYY-MM" string).
When we roll over, `current_month_used` is reset and the appropriate quota
is "refreshed" (the quota is fixed; only the used counter rolls over).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Partner


def _month_ref(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def current_month_ref(now: datetime | None = None) -> str:
    return _month_ref(now or datetime.now(timezone.utc))


class Available:
    """Snapshot of a partner's credit situation for the current period."""

    def __init__(self, quota, topup, monthly_used):
        self.quota = quota
        self.topup = topup
        self.monthly_used = monthly_used
        self.remaining_monthly = max(0, quota - monthly_used)
        self.remaining_total = topup + self.remaining_monthly

    @property
    def can_extract(self) -> bool:
        return self.remaining_total > 0

    def to_dict(self) -> dict:
        return {
            "monthly_quota": self.quota,
            "topup_credits": self.topup,
            "current_month_used": self.monthly_used,
            "remaining_monthly": self.remaining_monthly,
            "remaining_total": self.remaining_total,
            "can_extract": self.can_extract,
        }


def rollover_if_needed(db: Session, partner: Partner, now: datetime | None = None) -> None:
    """Reset the monthly-used counter when the period changes. Idempotent."""
    ref = current_month_ref(now)
    if partner.current_month_ref != ref:
        partner.current_month_used = 0
        partner.current_month_ref = ref
        db.commit()


def availability(db: Session, partner: Partner, now: datetime | None = None) -> Available:
    """Return the partner's credit availability for the current period."""
    rollover_if_needed(db, partner, now)
    return Available(
        quota=partner.monthly_credit_quota or 0,
        topup=partner.topup_credits or 0,
        monthly_used=partner.current_month_used or 0,
    )


def charge_pages(db: Session, partner: Partner, pages: int, now: datetime | None = None) -> None:
    """Consume credits for `pages` scanned pages. Assumes availability() was
    already checked and rolled over. Consumes the top-up pool first, then
    the monthly quota. Does not allow going negative."""
    rollover_if_needed(db, partner, now)
    need = max(0, pages)
    if need == 0:
        return

    # Top-ups first.
    from_topup = min(partner.topup_credits or 0, need)
    partner.topup_credits = (partner.topup_credits or 0) - from_topup
    need -= from_topup

    # Then monthly quota (clamp to what remains in the period).
    remaining_monthly = max(0, (partner.monthly_credit_quota or 0) - (partner.current_month_used or 0))
    from_monthly = min(remaining_monthly, need)
    partner.current_month_used = (partner.current_month_used or 0) + from_monthly

    db.commit()