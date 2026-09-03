"""Idempotent Docker entrypoint.

Runs init_db (create tables) then ensures at least one partner exists so
the API is usable immediately on a fresh database. Prints any API key that
is (re)created so it can be wired into the frontend/sync-agent.

Delegates to uvicorn after setup.
"""

import os
import secrets
import subprocess
import sys

from app.db import SessionLocal, engine
from app.models import Base, Partner, User
from app.init_db import migrate
from app.security import hash_password


def bootstrap() -> None:
    Base.metadata.create_all(bind=engine)
    migrate()

    default_email = os.getenv("DEFAULT_CONSOLE_EMAIL", "").strip().lower()
    default_password = os.getenv("DEFAULT_CONSOLE_PASSWORD", "").strip()

    db = SessionLocal()
    try:
        existing = db.query(Partner).first()
        if existing is not None:
            print(f"[bootstrap] partner already exists: {existing.name}")
            # Ensure the demo user exists if creds were provided on a rerun.
            if default_email and default_password:
                _ensure_user(db, existing, default_email, default_password)
            return
        api_key = "hhx_" + secrets.token_urlsafe(32)
        db.add(Partner(name="Docker Default Partner", api_key=api_key))
        db.commit()
        partner = db.query(Partner).filter(Partner.api_key == api_key).first()
        print(f"[bootstrap] created default partner. API key: {api_key}")
        print("[bootstrap] use as:  Authorization: Bearer " + api_key)
        if default_email and default_password:
            _ensure_user(db, partner, default_email, default_password)
    finally:
        db.close()


def _ensure_user(db, partner: Partner, email: str, password: str) -> None:
    """Idempotently create an owner user for a partner."""
    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        print(f"[bootstrap] console user already exists: {email}")
        return
    db.add(User(
        partner_id=partner.id,
        email=email,
        name=partner.name,
        password_hash=hash_password(password),
        role="owner",
    ))
    db.commit()
    print(f"[bootstrap] created console user: {email} (owner)")


if __name__ == "__main__":
    bootstrap()
    cmd = sys.argv[1:] or ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    os.execvp(cmd[0], cmd)
