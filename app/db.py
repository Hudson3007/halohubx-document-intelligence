"""
SQLAlchemy engine + session. Synchronous (not async) on purpose — the
main I/O-bound work here (calling the AI provider, delivering webhooks)
already happens as separate HTTP calls, so sync SQLAlchemy keeps this
MVP simpler without a meaningful performance cost at this scale.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Add it to your .env file locally, or to "
        "your host's environment variables in production. Example (Supabase): "
        "postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres"
    )

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """FastAPI dependency — yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
