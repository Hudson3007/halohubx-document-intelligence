"""Password hashing for console user accounts.

Uses PBKDF2-HMAC-SHA256 (stdlib hashlib) — a modern, salted KDF with no native
dependency, so the API image stays slim and builds with plain `pip install`.
Format:  pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>
"""

import base64
import hashlib
import hmac
import os
import threading
import time

from app.config import AUTH_RATE_LIMIT_PER_MIN

ITERATIONS = 260_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return "$".join([
        _ALGO,
        str(ITERATIONS),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    ])


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


class RateLimiter:
    """A minimal fixed-window, per-client, in-process rate limiter.

    Good enough to blunt credential-stuffing / signup-spray against the
    single uvicorn worker this MVP runs. It is NOT a substitute for a shared
    limiter (Redis) or an edge gateway in multi-worker production — that is a
    Phase-3+ concern. Buckets are pruned on access to bound memory.
    """

    def __init__(self, per_window: int = AUTH_RATE_LIMIT_PER_MIN, window_seconds: int = 60):
        self.per_window = per_window
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._buckets.get(key, [])
            # Drop out-of-window timestamps.
            hits = [t for t in hits if t > cutoff]
            if len(hits) >= self.per_window:
                self._buckets[key] = hits
                return False
            hits.append(now)
            self._buckets[key] = hits
            return True


# Shared instance for the auth endpoints (in-process, single worker).
auth_limiter = RateLimiter()