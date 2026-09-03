"""Password hashing for console user accounts.

Uses PBKDF2-HMAC-SHA256 (stdlib hashlib) — a modern, salted KDF with no native
dependency, so the API image stays slim and builds with plain `pip install`.
Format:  pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>
"""

import base64
import hashlib
import hmac
import os

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