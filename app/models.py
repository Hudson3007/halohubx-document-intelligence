"""
Multi-tenant schema.

Partner  = an integration partner (e.g. Vamsi, Yahya) reselling to their
           own sub-clients under a revenue-share model.
Client   = one of a partner's end customers (or a partner's own direct
           usage, if they don't sub-resell). All documents belong to a
           Client, which belongs to a Partner — this is the isolation
           boundary: a partner can only ever see their own clients' data.
Document = one uploaded file and its extraction lifecycle.

Run `python -m app.init_db` once (after DATABASE_URL is set) to create
these tables. For anything beyond this MVP schema (adding columns later,
etc.) you'll want a real migration tool like Alembic — this simple
create_all() approach doesn't handle schema changes safely once you have
production data in the tables.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Text, Float, Boolean, Integer, DateTime, ForeignKey, JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


class Partner(Base):
    __tablename__ = "partners"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    api_key = Column(String, unique=True, nullable=False, index=True)
    default_webhook_url = Column(String, nullable=True)
    zero_data_retention = Column(Boolean, default=False)

    # --- Usage / credit metering ---
    # Pricing model: 1 credit = 1 PDF page scanned.
    #   monthly_credit_quota : credits granted every reset period (resets)
    #   topup_credits        : one-time purchased/add-on pool (never resets)
    #   current_month_used   : pages charged in the current period
    #   current_month_ref    : e.g. "2026-09" — whose usage current_month_used counts
    # Extraction proceeds while (topup_credits + (quota - current_month_used)) > 0.
    # Top-ups are consumed first, then the monthly quota.
    monthly_credit_quota = Column(Integer, default=1000)
    topup_credits = Column(Integer, default=0)
    current_month_used = Column(Integer, default=0)
    current_month_ref = Column(String, nullable=True)

    # --- Billing / payments (Phase 3b) ---
    # Subscription plan id (see config.PLAN_QUOTAS) governing the monthly quota
    # that resets each period, plus the Razorpay-side ids for the customer and
    # the active subscription so we can reconcile webhooks.
    plan = Column(String, default="starter", nullable=False)
    razorpay_customer_id = Column(String, nullable=True)
    subscription_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    clients = relationship("Client", back_populates="partner")


class Client(Base):
    __tablename__ = "clients"

    id = Column(String, primary_key=True, default=_uuid)
    partner_id = Column(String, ForeignKey("partners.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    partner = relationship("Partner", back_populates="clients")
    documents = relationship("Document", back_populates="client")


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=_uuid)
    partner_id = Column(String, ForeignKey("partners.id"), nullable=False, index=True)
    client_id = Column(String, ForeignKey("clients.id"), nullable=False, index=True)

    filename = Column(String, nullable=False)
    status = Column(String, default="pending")  # pending | processing | completed | failed
    page_count = Column(Integer, default=0)  # pages scanned (1 credit each)
    result_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    webhook_url = Column(String, nullable=True)
    webhook_delivered = Column(Boolean, default=False)

    # Soft delete: when a user removes a document it's flagged here (hidden
    # from every list) and either purged after the owner's undo window or
    # kept for the owner's bin approval. Kept instead of a hard DELETE so a
    # mistake can be reverted and so an analyst's request hits the owner's
    # approval queue first.
    deleted_at = Column(DateTime, nullable=True)
    delete_requested_by = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    client = relationship("Client", back_populates="documents")


class User(Base):
    """A human account belonging to a Partner, used to log in to the console.

    Distinct from the Partner's machine API key (app.auth.get_current_partner),
    which stays the credential for server-to-server API calls. A User owns a
    role ('owner' or 'analyst') for the console's RBAC: owners can manage
    credits/settings, analysts can review & approve only.
    """

    __tablename__ = "users"

    id = Column(String, primary_key=True, default=_uuid)
    partner_id = Column(String, ForeignKey("partners.id"), nullable=False, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)  # pbkdf2_hmac; see app.security
    role = Column(String, default="owner", nullable=False)  # owner | analyst
    session_token = Column(String, unique=True, nullable=True, index=True)  # console bearer token
    invite_token = Column(String, unique=True, nullable=True, index=True)  # one-time invite link
    invite_accepted = Column(Boolean, default=False)

    # --- Session + login hardening ---
    # A console session token is a bearer credential; an expired or revoked
    # token is rejected by app.auth.get_actor before any data is served.
    session_expires_at = Column(DateTime, nullable=True)   # None => never expires (legacy API-key style)
    session_revoked = Column(Boolean, default=False)       # explicit revocation
    # Brute-force protection on the password login endpoint.
    login_failed_attempts = Column(Integer, default=0)     # consecutive failures
    login_locked_until = Column(DateTime, nullable=True)   # lockout window after too many failures

    created_at = Column(DateTime, default=datetime.utcnow)

    partner = relationship("Partner")


class AuditLogEntry(Base):
    """A chronological record of human + machine actions on a partner's data.

    Written whenever a review is confirmed/approved, a document is uploaded,
    or a user logs in — so the console can show who did what and when.
    """

    __tablename__ = "audit_log"

    id = Column(String, primary_key=True, default=_uuid)
    partner_id = Column(String, ForeignKey("partners.id"), nullable=False, index=True)
    document_id = Column(String, nullable=True, index=True)
    actor_email = Column(String, nullable=True)   # "user@x.com" or partner name for api-key
    actor_role = Column(String, nullable=True)    # owner | analyst | api_key | system
    action = Column(String, nullable=False, index=True)  # upload | review_edit | approval | login
    summary = Column(Text, nullable=True)         # e.g. "Corrected 2 fields (qty, unit_price)"
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AiDailyUsage(Base):
    """Daily AI-request meter.

    Free-tier Gemini is capped around 20 requests/day; each document
    extraction consumes one. We keep a running per-provider count for the
    current UTC day so the API can fail fast on uploads (instead of charging
    credits and then failing extraction) and so the console can show how much
    of the daily budget is left. Not partner-scoped: the API key is shared
    server-side, so one row per (provider, day) is correct.
    """

    __tablename__ = "ai_daily_usage"
    __table_args__ = (UniqueConstraint("provider", "day", name="uq_ai_daily_usage_provider_day"),)

    id = Column(String, primary_key=True, default=_uuid)
    provider = Column(String, nullable=False, index=True)  # "gemini" | "claude"
    day = Column(String, nullable=False, index=True)       # UTC date "YYYY-MM-DD"
    requests = Column(Integer, nullable=False, default=0)  # requests used this day
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreditOrder(Base):
    """A purchase/ledger record for paid credits or a plan subscription.

    One row per Razorpay order:
      * order_type "topup" — buying a pack of credits (added to topup pool)
      * order_type "plan"  — a recurring plan subscription (sets monthly quota)

    status lifecycle: pending -> paid | failed. On a successful captured
    payment the webhook flips status to "paid" and applies the credits (or
    plan). Razorpay's order/payment/signature ids are kept for reconciliation
    and the webhook signature check.
    """

    __tablename__ = "credit_orders"

    id = Column(String, primary_key=True, default=_uuid)
    partner_id = Column(String, ForeignKey("partners.id"), nullable=False, index=True)
    order_type = Column(String, nullable=False, default="topup")  # topup | plan
    plan = Column(String, nullable=True)          # for plan orders: target plan id
    credits = Column(Integer, nullable=False, default=0)  # credits granted on payment
    amount_paise = Column(Integer, nullable=False, default=0)  # price paid (INR paise)
    razorpay_order_id = Column(String, nullable=True, index=True)
    razorpay_payment_id = Column(String, nullable=True)
    razorpay_signature = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending | paid | failed | refunded
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
