"""Phase 2: Webhook delivery with SSRF guard.

Simple synchronous retry loop — good enough for MVP volume. Once you're
processing enough documents that a slow/down partner endpoint could back
up requests, this should move to a background job queue (e.g. Celery or
RQ) instead of retrying inline during the request. Flagging that now so
it doesn't surprise you later — not needed at Phase 0/1/2 scale.

Every URL is validated by app.ssrf.validate_webhook_url before the first
POST, so no private/loopback/link-local/metadata host can be used as a
webhook target regardless of how the URL was set (upload form, partner
default, stored document webhook, etc.).
"""

import time

import httpx

from app.config import WEBHOOK_MAX_RETRIES
from app.ssrf import validate_webhook_url


def deliver_webhook(url: str, payload: dict) -> bool:
    """Attempts to POST payload to url, retrying with backoff. Returns
    True if delivered successfully (2xx response), False otherwise."""
    if not url:
        return False

    try:
        validate_webhook_url(url)
    except ValueError:
        return False

    for attempt in range(1, WEBHOOK_MAX_RETRIES + 1):
        try:
            response = httpx.post(url, json=payload, timeout=15)
            if 200 <= response.status_code < 300:
                return True
        except Exception:
            pass  # network error, timeout, etc. — just retry

        if attempt < WEBHOOK_MAX_RETRIES:
            time.sleep(2 ** attempt)  # 2s, 4s, 8s...

    return False
