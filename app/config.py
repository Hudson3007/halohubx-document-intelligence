"""
Central config. All values come from environment variables (.env locally,
or the host's environment variable settings in production — e.g. Render's
Environment tab). Never hardcode secrets here.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Example Supabase Postgres URL shape:
# postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres

# --- Database security guardrails (see app/db.py) ---
# Force TLS for remote (non-loopback) Postgres connections unless this is set.
DB_SSL_DISABLE = os.environ.get("DB_SSL_DISABLE", "false").lower() in ("true", "1", "yes")
# Refuse to connect as a database superuser (postgres/admin/root) unless set.
ALLOW_SUPERUSER_DB = os.environ.get("ALLOW_SUPERUSER_DB", "false").lower() in ("true", "1", "yes")

DEFAULT_AI_PROVIDER = os.environ.get("DEFAULT_AI_PROVIDER", "gemini")  # "claude" or "gemini"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# How many times to retry a webhook delivery before giving up.
WEBHOOK_MAX_RETRIES = int(os.environ.get("WEBHOOK_MAX_RETRIES", "3"))

# Below this confidence, a field should be flagged for human review in the
# HITL staging UI (Phase 3). Stored here so both the API and that UI agree
# on the same threshold.
LOW_CONFIDENCE_THRESHOLD = float(os.environ.get("LOW_CONFIDENCE_THRESHOLD", "0.85"))

# --- Upload safety ---
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))  # 25 MB default

# --- Webhook SSRF guard ---
ALLOW_HTTP_LOOPBACK_WEBHOOKS = os.environ.get("ALLOW_HTTP_LOOPBACK_WEBHOOKS", "false").lower() in ("true", "1", "yes")

# --- Observability ---
# Emit one JSON object per log line (container default); set LOG_JSON=0 for
# human-readable text in local dev. LOG_LEVEL is the root log level.
LOG_JSON = os.environ.get("LOG_JSON", "1").lower() not in ("0", "false", "no")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# --- Auth hardening ---
# Console session tokens expire after this many hours (re-issue on login).
SESSION_TOKEN_TTL_HOURS = int(os.environ.get("SESSION_TOKEN_TTL_HOURS", "12"))
# Password login is locked out after LOGIN_MAX_ATTEMPTS consecutive failures.
LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
# Lockout window (minutes) once the failure cap is reached.
LOGIN_LOCKOUT_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", "15"))
# Coarse in-process rate cap for auth endpoints (requests/min per client IP). This
# is a belt-and-braces measure; a reverse proxy (nginx/CloudFront) is the real
# enforcement surface in production.
AUTH_RATE_LIMIT_PER_MIN = int(os.environ.get("AUTH_RATE_LIMIT_PER_MIN", "20"))

# Where the original PDF is stored so the HITL / Review Queue UI can show a
# side-by-side preview of the source while a human corrects the extraction.
# NOTE: this means the API now retains source files (a deliberate choice to
# support human review); it is no longer zero-retention by default.
UPLOAD_DIR = os.environ.get(
    "UPLOAD_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "uploads"),
)

# --- Startup diagnostic ---
# Prints once when the app starts, so you can see in the uvicorn terminal
# exactly what this running process actually loaded — removes any doubt
# about stale terminals, wrong working directories, etc. Never prints the
# actual key values, just whether they were found.
print(
    f"[startup] .env path resolved to: {ENV_PATH}  (exists={ENV_PATH.exists()})"
)
print(
    f"[startup] DEFAULT_AI_PROVIDER={DEFAULT_AI_PROVIDER!r}  "
    f"GEMINI_API_KEY set={bool(GEMINI_API_KEY)}  "
    f"ANTHROPIC_API_KEY set={bool(ANTHROPIC_API_KEY)}  "
    f"DATABASE_URL set={bool(DATABASE_URL)}"
)