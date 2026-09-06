"""
Human console auth — distinct from the machine API-key auth in app/auth.py.

These endpoints let a partner sign up / log in to the web console with an
email + password. On success we return the partner's *API key*, which the
console then uses as `Authorization: Bearer <key>` for every backend call
(upload, documents, usage, ...). This keeps one credential model at the API
layer while giving humans a real login on top.

  POST /auth/signup  {name?, email, password}  -> creates Partner + owner User
  POST /auth/login   {email, password}          -> returns api_key + user

Roles: 'owner' (manage), 'analyst' (review/approve). See app.models.User.
"""

import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_actor, require_role, Actor, issue_session
from app.config import LOGIN_MAX_ATTEMPTS, LOGIN_LOCKOUT_MINUTES
from app.db import get_db
from app.models import Partner, User
from app.security import hash_password, verify_password, auth_limiter

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now() -> datetime:
    return datetime.utcnow()


def _client_key(request: Request) -> str:
    """Best-effort client identifier for rate limiting: X-Forwarded-For first
    hop (set by a proxy) or the direct peer address, else a loopback fallback."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _rate_limited(request: Request) -> None:
    if not auth_limiter.allow(_client_key(request)):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again shortly.",
        )


class SignupRequest(BaseModel):
    name: str | None = None
    email: str
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: str
    password: str


def _validate_email(email: str) -> None:
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Please provide a valid email address.")


def _public_user(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role}


@router.post("/signup")
def signup(
    req: SignupRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    _rate_limited(request)
    _validate_email(req.email)
    existing = db.query(User).filter(User.email == req.email.lower()).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    import secrets

    api_key = "hhx_" + secrets.token_urlsafe(32)
    partner = Partner(name=req.name or req.email.split("@")[0], api_key=api_key)
    db.add(partner)
    db.flush()

    user = User(
        partner_id=partner.id,
        email=req.email.lower(),
        name=req.name or req.email.split("@")[0],
        password_hash=hash_password(req.password),
        role="owner",
        session_token=None,  # set by issue_session below
    )
    db.add(user)
    db.flush()
    session_token = issue_session(user)
    db.commit()
    db.refresh(partner)
    db.refresh(user)

    return {
        "session_token": session_token,
        "partner_id": partner.id,
        "partner_name": partner.name,
        "user": _public_user(user),
    }


@router.post("/login")
def login(
    req: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    _rate_limited(request)
    user = db.query(User).filter(User.email == req.email.lower()).first()

    # Uniform error for unknown user vs wrong password (don't leak account
    # existence), but only run the PBKDF2 check when there's a real user so we
    # don't waste hashes on spray attempts.
    now = _now()

    def _reject() -> HTTPException:
        return HTTPException(status_code=401, detail="Invalid email or password.")

    if user is None:
        raise _reject()

    # Enforce the lockout window if active.
    if user.login_locked_until is not None and user.login_locked_until > now:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again after the lockout window.",
        )

    if not verify_password(req.password, user.password_hash):
        user.login_failed_attempts = (user.login_failed_attempts or 0) + 1
        locked = user.login_failed_attempts >= LOGIN_MAX_ATTEMPTS
        if locked:
            user.login_locked_until = now + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
            user.login_failed_attempts = 0  # reset counter; window governs
            db.commit()
            raise HTTPException(
                status_code=429,
                detail=f"Account temporarily locked after {LOGIN_MAX_ATTEMPTS} failed attempts. Try again later.",
            )
        db.commit()
        raise _reject()

    # Success: clear any lockout state and rotate the session token.
    user.login_failed_attempts = 0
    user.login_locked_until = None

    partner = db.query(Partner).filter(Partner.id == user.partner_id).first()
    if partner is None:
        raise HTTPException(status_code=404, detail="Partner account not found.")

    session_token = issue_session(user)
    db.commit()

    # Audit: record the login.
    from app.audit import record
    record(
        db, partner_id=partner.id, action="login",
        actor_email=user.email, actor_role=user.role,
        summary="Signed in to partner console",
    )
    db.commit()
    db.refresh(user)

    return {
        "session_token": session_token,
        "partner_id": partner.id,
        "partner_name": partner.name,
        "user": _public_user(user),
    }


@router.post("/logout")
def logout(
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """Revoke the caller's current console session. API-key callers are
    no-ops (they have no session to revoke). The token is invalidated
    immediately server-side."""
    if actor.source != "session":
        return {"revoked": False, "detail": "No console session to revoke."}
    user = db.query(User).filter(
        User.partner_id == actor.partner_id,
        User.email == actor.email,
    ).first()
    if user is None:
        return {"revoked": False, "detail": "Session not found."}
    user.session_revoked = True
    user.session_token = None          # stop accepting the token entirely
    user.session_expires_at = None
    db.commit()

    from app.audit import record
    record(
        db, partner_id=actor.partner_id, action="logout",
        actor_email=actor.email, actor_role=actor.role,
        summary="Signed out of partner console",
    )
    db.commit()
    return {"revoked": True}


def _new_invite_token() -> str:
    return "hhx_invite_" + secrets.token_urlsafe(32)


class MemberRequest(BaseModel):
    name: str | None = None
    email: str
    role: str = "analyst"  # owner | analyst


def _partner_for_actor(db: Session, actor: Actor) -> Partner:
    partner = db.query(Partner).filter(Partner.id == actor.partner_id).first()
    if partner is None:
        raise HTTPException(status_code=404, detail="Partner account not found.")
    return partner


@router.get("/partner")
def get_partner(
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Owner-only: fetch this partner's machine API key + billing snapshot.

    The key is deliberately NOT returned at signup/login/invite-accept (that
    was a privilege-escalation vector: an analyst handed the owner-level key
    could call everything as owner). Console humans authenticate with their
    session token; only an owner can read the machine key here — e.g. to wire
    up their ERP / sync agent.
    """
    partner = _partner_for_actor(db, actor)
    payload = {
        "api_key": partner.api_key,
        "partner_id": partner.id,
        "partner_name": partner.name,
        "default_webhook_url": partner.default_webhook_url,
    }
    from app.billing import availability
    payload["credits"] = availability(db, partner).to_dict()
    return payload


@router.get("/partner/members")
def list_members(
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """List the human accounts on this partner's workspace. Owner-only."""
    _partner_for_actor(db, actor)
    users = db.query(User).filter(User.partner_id == actor.partner_id).all()
    return {"members": [dict(_public_user(u), created_at=u.created_at.isoformat()) for u in users]}


@router.post("/partner/members")
def create_member(
    req: MemberRequest,
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Owner invites a teammate (default role: analyst). The invitee sets their
    own password via the returned invite link — the owner never sees it."""
    _partner_for_actor(db, actor)
    if req.role not in ("owner", "analyst"):
        raise HTTPException(status_code=400, detail="role must be 'owner' or 'analyst'.")
    _validate_email(req.email)
    existing = db.query(User).filter(User.email == req.email.lower()).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A user with this email already exists.")

    user = User(
        partner_id=actor.partner_id,
        email=req.email.lower(),
        name=req.name or req.email.split("@")[0],
        password_hash="",                       # not set until the invite is accepted
        role=req.role,
        invite_token=_new_invite_token(),
        invite_accepted=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    from app.audit import record
    record(
        db, partner_id=actor.partner_id, action="member_added",
        actor_email=actor.email, actor_role=actor.role,
        summary=f"Invited {user.email} as {user.role}",
    )
    db.commit()

    invite_link = f"/auth/invite/{user.invite_token}"
    return {"member": _public_user(user), "invite_link": invite_link}


class MemberUpdateRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    role: str | None = None


@router.patch("/partner/members/{user_id}")
def update_member(
    user_id: str,
    req: MemberUpdateRequest,
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Owner edits a teammate's name, email, or role."""
    _partner_for_actor(db, actor)
    user = db.query(User).filter(
        User.id == user_id, User.partner_id == actor.partner_id
    ).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Member not found.")

    if req.email:
        _validate_email(req.email)
        new_email = req.email.lower()
        if new_email != user.email:
            clash = db.query(User).filter(User.email == new_email).first()
            if clash is not None:
                raise HTTPException(status_code=409, detail="A user with this email already exists.")
            user.email = new_email

    if req.name is not None:
        user.name = req.name.strip() or user.name

    if req.role is not None:
        if req.role not in ("owner", "analyst"):
            raise HTTPException(status_code=400, detail="role must be 'owner' or 'analyst'.")
        if str(user.id) == str(actor.user_id):
            raise HTTPException(status_code=400, detail="You cannot change your own role.")
        user.role = req.role

    db.commit()
    db.refresh(user)

    from app.audit import record_from_actor
    record_from_actor(
        db, actor=actor, action="member_updated",
        summary=f"Updated member {user.email}",
    )
    db.commit()
    return {"member": _public_user(user)}


@router.delete("/partner/members/{user_id}")
def remove_member(
    user_id: str,
    actor: Actor = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Owner removes a teammate from the workspace."""
    _partner_for_actor(db, actor)
    user = db.query(User).filter(
        User.id == user_id, User.partner_id == actor.partner_id
    ).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Member not found.")
    if str(user.id) == str(actor.user_id):
        raise HTTPException(status_code=400, detail="You cannot remove your own account.")

    removed_email = user.email
    db.delete(user)
    db.commit()

    from app.audit import record_from_actor
    record_from_actor(
        db, actor=actor, action="member_removed",
        summary=f"Removed member {removed_email}",
    )
    db.commit()
    return {"ok": True, "removed": removed_email}


class InviteAcceptRequest(BaseModel):
    name: str | None = None
    password: str = Field(min_length=8)


@router.get("/invite/{token}")
def invite_info(
    token: str,
    db: Session = Depends(get_db),
):
    """Public: given an invite token, say who it's for and what role they'll get
    (so the accept page can pre-fill). Does not leak passwords or the partner
    API key. The token is cleared once accepted, so used links 404."""
    user = db.query(User).filter(User.invite_token == token).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Invite not found or already used.")
    return {"email": user.email, "name": user.name, "role": user.role}


@router.post("/invite/{token}/accept")
def accept_invite(
    token: str,
    req: InviteAcceptRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Public: the invitee sets their own name + password, completing onboarding.
    Issues a session token so they can be signed in immediately. One-time use."""
    _rate_limited(request)
    user = db.query(User).filter(User.invite_token == token).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Invite not found or already used.")

    partner = db.query(Partner).filter(Partner.id == user.partner_id).first()
    if partner is None:
        raise HTTPException(status_code=404, detail="Partner account not found.")

    user.password_hash = hash_password(req.password)
    if req.name:
        user.name = req.name
    user.invite_accepted = True
    user.invite_token = None                  # one-time use
    user.login_failed_attempts = 0
    user.login_locked_until = None
    session_token = issue_session(user)       # signed straight in
    db.commit()
    db.refresh(user)

    from app.audit import record
    record(
        db, partner_id=partner.id, action="member_joined",
        actor_email=user.email, actor_role=user.role,
        summary=f"Accepted invite to join as {user.role}",
    )
    db.commit()

    return {
        "session_token": session_token,
        "partner_id": partner.id,
        "partner_name": partner.name,
        "user": _public_user(user),
    }