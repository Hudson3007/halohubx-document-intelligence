"""
Lightweight per-partner API key auth. Partners authenticate with:
    Authorization: Bearer <their api_key>

Uses FastAPI's built-in HTTPBearer security scheme (rather than reading
the header manually) specifically so the /docs page renders a proper
"Authorize" button instead of a raw header field per endpoint.

This is intentionally simple for Phase 0/1/2 — no OAuth, no user
accounts, just a static key per partner (like Stripe's early API did).
Good enough for server-to-server ERP integration calls. If you later add
a partner dashboard with login (Phase 4/5), that's a separate, additional
auth layer for humans — this key-based auth for the API itself can stay.
"""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Partner, User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_partner(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Partner:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header. Use: Bearer <api_key>")

    api_key = credentials.credentials
    partner = db.query(Partner).filter(Partner.api_key == api_key).first()
    if partner is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return partner


class Actor:
    """A resolved caller: either a machine API key (role=owner, backward
    compatible) or a signed-in console user (role from their account)."""

    def __init__(self, partner_id: str, role: str, source: str, email: str | None = None):
        self.partner_id = partner_id
        self.role = role
        self.source = source  # "api_key" | "session"
        self.email = email

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def get_actor(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Actor:
    """Resolve the bearer token to an Actor.

    The same token namespace is used for both credential kinds: a Partner
    api_key (server-to-server, treated as owner) or a User.session_token
    (console login, carries the user's real role). This lets RBAC apply to
    console calls without breaking ERP / sync-agent / Office-AI callers that
    only have a machine key.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header. Use: Bearer <token>")

    token = credentials.credentials
    partner = db.query(Partner).filter(Partner.api_key == token).first()
    if partner is not None:
        return Actor(partner_id=partner.id, role="owner", source="api_key", email=partner.name)

    user = db.query(User).filter(User.session_token == token).first()
    if user is not None:
        return Actor(partner_id=user.partner_id, role=user.role, source="session", email=user.email)

    raise HTTPException(status_code=401, detail="Invalid token")


def require_role(*roles: str):
    """Dependency factory: guard a console/owner action by role.

    Usage: `actor: Actor = Depends(require_role("owner"))`
    Analysts get 403 on owner-only actions; machine API keys (role=owner)
    always pass so existing integrations keep working.
    """

    def _guard(actor: Actor = Depends(get_actor)) -> Actor:
        if actor.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Your role ({actor.role}) is not allowed to perform this action.",
            )
        return actor

    return _guard