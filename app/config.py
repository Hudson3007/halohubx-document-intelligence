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

# --- Daily AI request quota ---
# Free-tier Gemini is capped at ~20 requests/day; a document extraction is one
# request. Tracked per-day (UTC) so uploads fail fast instead of being charged
# and then failing extraction, and so the UI can show remaining capacity.
# Override per provider via env if you upgrade keys.
AI_DAILY_LIMITS = {
    "gemini": int(os.environ.get("GEMINI_DAILY_LIMIT", "20")),
    "claude": int(os.environ.get("CLAUDE_DAILY_LIMIT", "200")),
}

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
#
# On hosts with an ephemeral filesystem (Render free tier wipes disk on
# restart/sleep/redeploy), set UPLOAD_STORAGE=supabase to persist PDFs in a
# Supabase Storage bucket instead of local disk.
UPLOAD_DIR = os.environ.get(
    "UPLOAD_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "uploads"),
)
UPLOAD_STORAGE = os.environ.get("UPLOAD_STORAGE", "disk").lower()  # "disk" or "supabase"
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "halohubx-uploads")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# --- Billing / payments (Phase 3b) ---
# Pricing: 1 credit = 1 PDF page. Purchased credits are added to the partner's
# top-up pool (never resets). Subscriptions set the monthly quota per period.
#
# Price per credit, in Indian paise (100 paise = INR 1). This is a PLACEHOLDER
# rate for Profezzo to set — change CREDIT_PRICE_PAISE and rebuild/restart.
CREDIT_PRICE_PAISE = int(os.environ.get("CREDIT_PRICE_PAISE", "200"))  # 200 paise = INR 2/credit (placeholder)
PAYMENTS_CURRENCY = os.environ.get("PAYMENTS_CURRENCY", "INR")

# Razorpay merchant keys. Empty => the /billing/dev/simulate endpoint (below)
# can stand in for a real checkout so the whole flow is testable without a
# merchant account. Never commit real keys.
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# Subscription plans. Each key is a plan id; value is the monthly credit quota
# that plan grants every period. The free tier ("starter") is the default for
# every partner; paying plans raise the quota.
PLAN_QUOTAS = {
    "starter": int(os.environ.get("PLAN_QUOTA_STARTER", "1000")),
    "growth": int(os.environ.get("PLAN_QUOTA_GROWTH", "5000")),
    "scale": int(os.environ.get("PLAN_QUOTA_SCALE", "20000")),
}

# Dev / simulation gate: when set (default ON in local dev), expose
# POST /billing/dev/simulate so you can grant credits or switch plans without a
# real Razorpay merchant. Disabled in production. Never enable in prod.
ENABLE_DEV_PAYMENTS = os.environ.get("ENABLE_DEV_PAYMENTS", "true").lower() in ("true", "1", "yes")

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
print(
    f"[startup] RAZORPAY_KEY_ID set={bool(RAZORPAY_KEY_ID)}  "
    f"CREDIT_PRICE_PAISE={CREDIT_PRICE_PAISE}  "
    f"ENABLE_DEV_PAYMENTS={ENABLE_DEV_PAYMENTS}"
)