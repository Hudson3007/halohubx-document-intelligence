"""
Run once to create all tables in your database:
    python -m app.init_db

Safe to re-run — create_all() skips tables that already exist, and the
migrate() helper below idempotently adds columns that were introduced after
the first deploy (see MIGRATIONS). It does NOT alter existing columns or
drop anything; for structural changes you'll want Alembic once this is in
heavy production.
"""

from sqlalchemy import text

from app.db import engine
from app.models import Base

# Idempotent column additions for databases created before these exact
# statements existed. Postgres supports ADD COLUMN IF NOT EXISTS.
MIGRATIONS = [
    "ALTER TABLE partners ADD COLUMN IF NOT EXISTS monthly_credit_quota INTEGER DEFAULT 1000",
    "ALTER TABLE partners ADD COLUMN IF NOT EXISTS topup_credits INTEGER DEFAULT 0",
    "ALTER TABLE partners ADD COLUMN IF NOT EXISTS current_month_used INTEGER DEFAULT 0",
    "ALTER TABLE partners ADD COLUMN IF NOT EXISTS current_month_ref VARCHAR",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_count INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS session_token VARCHAR UNIQUE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS invite_token VARCHAR UNIQUE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS invite_accepted BOOLEAN DEFAULT FALSE",
]


def migrate() -> None:
    with engine.begin() as conn:
        for stmt in MIGRATIONS:
            conn.execute(text(stmt))


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    migrate()
    print("Tables created (or already existed) and migrations applied.")
