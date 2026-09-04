"""
Razorpay billing for paid credits (Phase 3b).

Pricing model (mixed plans + top-ups):
  * 1 credit = 1 PDF page.
  * A partner has a monthly quota (reset each period) determined by their
    subscription plan, PLUS a top-up pool of bought credits that never resets.
  * Both are priced through a single rate knob: CREDIT_PRICE_PAISE (paise per
    credit). A plan's monthly price is (quota * CREDIT_PRICE_PAISE); a top-up
    pack is (credits * CREDIT_PRICE_PAISE).

Flow (real merchant):
  1. Owner calls POST /billing/checkout/topup {credits} -> a Razorpay order is
     created and a CreditOrder rows is recorded as "pending".
  2. The frontend launches Razorpay Checkout with the returned order_id.
  3. After payment Razorpay either (a) sends a "payment.captured" webhook, or
     (b) the frontend calls POST /billing/payments/verify with the signature.
     Both paths are idempotent: the CreditOrder flips to "paid" exactly once
     and the credits are added to the top-up pool.
  4. Owner can re-read GET /billing for the updated balance + order history.

Dev / demo mode: when RAZORPAY_KEY_ID is not configured, real Razorpay calls
can't run. ENABLE_DEV_PAYMENTS (default ON locally) exposes POST
/billing/dev/simulate so the whole billing loop is testable without a
merchant. It is a dev-only escape hatch and 404s when disabled.
"""

import hashlib
import hmac

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import require_role, get_actor, Actor
from app.billing import availability
from app.config import (
    CREDIT_PRICE_PAISE,
    PAYMENTS_CURRENCY,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_WEBHOOK_SECRET,
    PLAN_QUOTAS,
    ENABLE_DEV_PAYMENTS,
)
from app.db import get_db
from app.logging_setup import get_logger
from app.models import Partner, CreditOrder

router = APIRouter()
log = get_logger("payments")

RAZORPAY_BASE = "https://api.razorpay.com"


def _razorpay_enabled() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def plan_price_paise(plan: str) -> int:
    """Monthly price of a plan at the shared per-credit rate (placeholder)."""
    return int((PLAN_QUOTAS.get(plan) or 0) * CREDIT_PRICE_PAISE)


def plans() -> list[dict]:
    """Public plan catalogue (id, monthly quota, monthly price, currency)."""
    out = []
    for pid, quota in PLAN_QUOTAS.items():
        out.append(
            {
                "id": pid,
                "monthly_quota": quota,
                "monthly_price_paise": plan_price_paise(pid),
                "currency": PAYMENTS_CURRENCY,
            }
        )
    return out


def _partner_for_actor(db: Session, actor: Actor) -> Partner:
    partner = db.query(Partner).filter(Partner.id == actor.partner_id).first()
    if partner is None:
        raise HTTPException(status_code=401, detail="Partner account not found.")
    return partner


# --------------------------------------------------------------------------
# Razorpay HTTP client
# --------------------------------------------------------------------------
def _rp_post(path: str, payload: dict) -> dict:
    if not _razorpay_enabled():
        raise HTTPException(
            status_code=503,
            detail="Razorpay is not configured (RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET missing). "
                   "Use the dev simulate endpoint (POST /billing/dev/simulate) when ENABLE_DEV_PAYMENTS=1.",
        )
    try:
        r = httpx.post(
            f"{RAZORPAY_BASE}{path}",
            json=payload,
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        log.error("razorpay http error", extra={"path": path, "error": str(e)})
        raise HTTPException(status_code=502, detail="Payments gateway unreachable.")
    if r.status_code >= 400:
        log.error("razorpay api error", extra={"path": path, "status": r.status_code, "body": r.text})
        raise HTTPException(status_code=502, detail=f"Payments gateway error: {r.status_code}")
    return r.json()


def _verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _apply_credits(db: Session, partner: Partner, credits: int) -> None:
    partner.topup_credits = (partner.topup_credits or 0) + max(0, credits)
    db.commit()


def _apply_plan(db: Session, partner: Partner, plan: str) -> None:
    quota = PLAN_QUOTAS.get(plan)
    if quota is None:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")
    partner.plan = plan
    partner.monthly_credit_quota = quota
    db.commit()


# --------------------------------------------------------------------------
# Public / ownership endpoints
# --------------------------------------------------------------------------
@router.get("/billing/plans")
def billing_plans():
    """Public catalogue of the subscription plans (id + quota + placeholder price)."""
    return {"currency": PAYMENTS_CURRENCY, "credit_price_paise": CREDIT_PRICE_PAISE, "plans": plans()}


@router.get("/billing")
def billing_snapshot(
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """Owner/analyst viewable billing snapshot + recent order history."""
    partner = _partner_for_actor(db, actor)
    avail = availability(db, partner)
    orders = (
        db.query(CreditOrder)
        .filter(CreditOrder.partner_id == partner.id)
        .order_by(CreditOrder.created_at.desc())
        .limit(25)
        .all()
    )
    return {
        "merchant_enabled": _razorpay_enabled(),
        "dev_enabled": ENABLE_DEV_PAYMENTS,
        "currency": PAYMENTS_CURRENCY,
        "credit_price_paise": CREDIT_PRICE_PAISE,
        "plan": partner.plan,
        "plan_price_paise": plan_price_paise(partner.plan),
        "usage": avail.to_dict(),
        "orders": [
            {
                "id": o.id,
                "type": o.order_type,
                "plan": o.plan,
                "credits": o.credits,
                "amount_paise": o.amount_paise,
                "status": o.status,
                "razorpay_order_id": o.razorpay_order_id,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ],
    }


@router.post("/billing/checkout/topup")
def checkout_topup(
    payload: dict,
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Create a Razorpay order to buy a pack of credits (adds to top-up pool)."""
    partner = _partner_for_actor(db, actor)
    try:
        credits = int(payload.get("credits") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="credits must be a positive integer")
    if credits <= 0:
        raise HTTPException(status_code=400, detail="credits must be a positive integer")

    amount_paise = credits * CREDIT_PRICE_PAISE
    rp_order = _rp_post(
        "/v1/orders",
        {
            "amount": amount_paise,
            "currency": PAYMENTS_CURRENCY,
            "receipt": f"topup_{partner.id[:8]}",
            "notes": {"partner_id": partner.id, "credits": credits},
        },
    )
    order = CreditOrder(
        partner_id=partner.id,
        order_type="topup",
        credits=credits,
        amount_paise=amount_paise,
        razorpay_order_id=rp_order.get("id"),
        status="pending",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    log.info("checkout_topup_created", extra={"order_id": order.id, "partner_id": partner.id, "credits": credits})
    return {
        "order_id": order.id,
        "razorpay_order_id": rp_order.get("id"),
        "amount_paise": amount_paise,
        "credits": credits,
        "currency": PAYMENTS_CURRENCY,
        "key_id": RAZORPAY_KEY_ID,
        "merchant_enabled": _razorpay_enabled(),
    }


@router.post("/billing/subscribe")
def subscribe(
    payload: dict,
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Start (or change to) a subscription plan. Creates a Razorpay customer +
    reading subscription in real mode; in dev mode just applies the plan.

    NOTE: for the launch this builds the Razorpay customer + plan + subscription
    and records them, but the actual recurring charge is reconciled via the
    subscription webhooks (subscription.charged / subscription.activated).
    """
    partner = _partner_for_actor(db, actor)
    plan = payload.get("plan")
    if plan not in PLAN_QUOTAS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")

    if _razorpay_enabled():
        # Real mode: idempotently create/refresh a Razorpay customer.
        if not partner.razorpay_customer_id:
            cust = _rp_post("/v1/customers", {"name": partner.name, "notes": {"partner_id": partner.id}})
            partner.razorpay_customer_id = cust.get("id")
            db.commit()
        rp_plan = _rp_post(
            "/v1/plans",
            {
                "period": "monthly",
                "interval": 1,
                "item": {
                    "name": f"HaloHubX {plan} plan",
                    "amount": plan_price_paise(plan),
                    "currency": PAYMENTS_CURRENCY,
                },
                "notes": {"partner_id": partner.id, "plan": plan},
            },
        )
        sub = _rp_post(
            "/v1/subscriptions",
            {
                "plan_id": rp_plan.get("id"),
                "customer_id": partner.razorpay_customer_id,
                "total_count": 12,
                "notes": {"plan": plan, "partner_id": partner.id},
            },
        )
        partner.subscription_id = sub.get("id")

    _apply_plan(db, partner, plan)
    order = CreditOrder(
        partner_id=partner.id,
        order_type="plan",
        plan=plan,
        credits=PLAN_QUOTAS[plan],
        amount_paise=plan_price_paise(plan),
        razorpay_order_id=partner.subscription_id,
        status="paid",
    )
    db.add(order)
    db.commit()
    log.info("subscribe_ok", extra={"partner_id": partner.id, "plan": plan})
    return {
        "plan": plan,
        "monthly_quota": PLAN_QUOTAS[plan],
        "amount_paise": plan_price_paise(plan),
        "currency": PAYMENTS_CURRENCY,
        "subscription_id": partner.subscription_id,
        "merchant_enabled": _razorpay_enabled(),
    }


@router.post("/billing/payments/verify")
def verify_payment(
    payload: dict,
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Verify a Razorpay payment signature and mark the CreditOrder paid.

    This is the client-side confirmation path (checkout page posts back the
    order/payment/signature trio). Idempotent: credits are applied exactly
    once even if the webhook also fires.
    """
    if not _razorpay_enabled():
        raise HTTPException(status_code=503, detail="Razorpay is not configured.")
    partner = _partner_for_actor(db, actor)
    order_id = payload.get("razorpay_order_id")
    payment_id = payload.get("razorpay_payment_id")
    signature = payload.get("razorpay_signature")
    if not all([order_id, payment_id, signature]):
        raise HTTPException(status_code=400, detail="Missing payment verification fields.")

    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode("utf-8"),
        f"{order_id}|{payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature.")

    order = (
        db.query(CreditOrder)
        .filter(CreditOrder.razorpay_order_id == order_id, CreditOrder.partner_id == partner.id)
        .first()
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.status == "paid":
        return {"already_paid": True, "credits_applied": 0}

    order.status = "paid"
    order.razorpay_payment_id = payment_id
    order.razorpay_signature = signature
    db.commit()
    _apply_credits(db, partner, order.credits)
    log.info("payment_verified", extra={"order_id": order.id, "partner_id": partner.id, "credits": order.credits})
    return {"already_paid": False, "credits_applied": order.credits}


@router.post("/billing/webhook")
async def payment_webhook(request: Request, db: Session = Depends(get_db)):
    """Razorpay webhook receiver (signature-verified, idempotent)."""
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Razorpay webhook secret not configured.")
    if not _verify_signature(body, signature, RAZORPAY_WEBHOOK_SECRET):
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    event = request.headers.get("X-Razorpay-Event", "")
    import json

    try:
        data = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON.")

    # payment.captured -> grant top-up credits for the matching order.
    if event == "payment.captured":
        payment = data.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payment.get("order_id")
        payment_id = payment.get("id")
        if not order_id:
            return {"received": True, "ignored": True, "reason": "no order_id"}
        order = db.query(CreditOrder).filter(CreditOrder.razorpay_order_id == order_id).first()
        if order is None:
            log.warning("webhook order not found", extra={"order_id": order_id})
            return {"received": True, "ignored": True, "reason": "order not found"}
        if order.status == "paid":
            return {"received": True, "already_paid": True}
        order.status = "paid"
        order.razorpay_payment_id = payment_id
        db.commit()
        partner = db.query(Partner).filter(Partner.id == order.partner_id).first()
        if partner and order.order_type == "topup":
            _apply_credits(db, partner, order.credits)
            log.info("webhook_credited", extra={"order_id": order.id, "credits": order.credits})
        return {"received": True, "already_paid": False}

    # subscription.charged / activated -> apply the plan to the partner.
    if event in ("subscription.charged", "subscription.activated", "subscription.completed"):
        entity = data.get("payload", {}).get("subscription", {}).get("entity", {})
        sub_id = entity.get("id")
        if not sub_id:
            return {"received": True, "ignored": True, "reason": "no subscription_id"}
        partner = db.query(Partner).filter(Partner.subscription_id == sub_id).first()
        if partner is None:
            return {"received": True, "ignored": True, "reason": "partner not found"}
        plan = (entity.get("notes") or {}).get("plan") or partner.plan
        _apply_plan(db, partner, plan)
        log.info("webhook_plan_applied", extra={"partner_id": partner.id, "plan": plan})
        return {"received": True, "applied": True}

    return {"received": True, "ignored": True, "reason": f"unhandled event {event}"}


# --------------------------------------------------------------------------
# Dev / demo only: grant credits or switch plan without a merchant.
# --------------------------------------------------------------------------
@router.post("/billing/dev/simulate")
def dev_simulate(
    payload: dict,
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """DEV-ONLY: grant credits and/or set a plan directly, for local + demo
    testing of the billing loop when no Razorpay merchant is configured.
    404s when ENABLE_DEV_PAYMENTS is off (must never be on in production)."""
    if not ENABLE_DEV_PAYMENTS:
        raise HTTPException(status_code=404, detail="Dev payments are disabled.")
    partner = _partner_for_actor(db, actor)

    if "credits" in payload:
        credits = int(payload.get("credits") or 0)
        if credits <= 0:
            raise HTTPException(status_code=400, detail="credits must be a positive integer")
        _apply_credits(db, partner, credits)
        order = CreditOrder(
            partner_id=partner.id,
            order_type="topup",
            credits=credits,
            amount_paise=credits * CREDIT_PRICE_PAISE,
            status="paid",
            razorpay_order_id="dev_simulate",
        )
        db.add(order)
        db.commit()

    if "plan" in payload and payload.get("plan") in PLAN_QUOTAS:
        _apply_plan(db, partner, payload["plan"])

    avail = availability(db, partner)
    return {
        "partner_id": partner.id,
        "plan": partner.plan,
        "topup_credits": partner.topup_credits,
        "usage": avail.to_dict(),
    }
