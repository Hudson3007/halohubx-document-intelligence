"""
Same provider-abstraction pattern as the Streamlit app's ai_client.py —
kept independent here since this backend is a separate service, but the
interface (.extract()) is identical on purpose so extraction logic reads
the same way in both places.
"""

import base64
import re
import time

from app.config import ANTHROPIC_API_KEY, GEMINI_API_KEY, DEFAULT_AI_PROVIDER


def _extract_retry_delay(exc: Exception) -> float | None:
    """Gemini's 429 payloads say 'Please retry in 21.17s' and/or
    'retry_delay { seconds: 21 }'. Return that (capped) so the caller waits
    the real window instead of a guess."""
    msg = str(exc)
    m = re.search(r"retry(?:_delay\s*\{\s*seconds:\s*| in )(\d+)", msg, re.IGNORECASE)
    if not m:
        return None
    try:
        return min(float(m.group(1)) + 1.0, 45.0)
    except ValueError:
        return None


def _is_rate_limited(exc: Exception) -> bool:
    """True if the error looks like a provider throttle/quota limit (429),
    server-busy or deadline timeout (502/503/504) — all transient and worth
    retrying — as opposed to a hard error (bad key, invalid PDF, etc)."""
    msg = str(exc)
    return (
        "429" in msg
        or "quota" in msg.lower()
        or "rate" in msg.lower()
        or "RESOURCE_EXHAUSTED" in msg
        or "DEADLINE_EXCEEDED" in msg
        or "504" in msg
        or "503" in msg
        or "502" in msg
        or "unavailable" in msg.lower()
        or "timeout" in msg.lower()
    )


def _is_daily_quota(exc: Exception) -> bool:
    """True if the 429 is a per-DAY free-tier cap (which no amount of
    waiting/retrying will fix — the quota resets at midnight UTC).

    Discriminate on the quota_id: daily limits are
    'GenerateRequestsPerDayPerProjectPerModel', per-minute ones are
    '...PerMinutePerProject...'. The shared metric name
    'generate_content_free_tier_requests' appears in BOTH, so it must not be
    used alone."""
    msg = str(exc)
    return "PerDay" in msg or "per day" in msg.lower()


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    if _is_daily_quota(exc):
        return "Daily provider quota exceeded (free tier allows only 20 requests/day). Retry tomorrow or upgrade the API key."
    if "429" in msg:
        return "Provider rate limit hit (429). Try again in a few minutes."
    return msg


def _retry_call(fn, *, attempts: int = 6, base_delay: float = 2.0, on_attempt=None):
    """Call fn() with backoff, retrying transient rate limits. Waits the
    provider-reported retry_delay when present (Gemini free tier 429s say
    'retry in 21s' — backing off only 2-16s never clears them). Fails fast
    on hard errors and per-DAY quota caps (retrying those wastes time).
    Raises the last error if it never succeeds.

    on_attempt (optional callable) runs before EVERY provider call (including
    retries) so the quota meter counts real usage: Google bills a free-tier
    cap against every attempt it receives, not once per document."""
    last_exc = None
    for attempt in range(attempts):
        if on_attempt is not None:
            on_attempt()
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - we re-raise at the end
            last_exc = exc
            if attempt >= attempts - 1 or not _is_rate_limited(exc):
                raise
            # Fail fast on the true daily cap — a per-minute throttle is the
            # only case worth waiting out, and the provider reports its delay.
            if _is_daily_quota(exc):
                raise
            delay = _extract_retry_delay(exc)
            if delay is None:
                delay = base_delay * (2 ** attempt) + (time.monotonic() % 1)
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _summarize_error(exc: Exception) -> str:
    """Short human-readable error to store on a failed Document (keeps the
    429 backtrace spam out of the DB)."""
    msg = _friendly_error(exc)
    return msg.splitlines()[0][:300]


class ClaudeClient:
    provider = "claude"

    def __init__(self, api_key: str, model: str = "claude-sonnet-5"):
        from anthropic import Anthropic

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def extract(self, file_bytes: bytes, media_type: str, prompt: str, on_attempt=None) -> str:
        b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
        doc_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }
        if on_attempt is not None:
            on_attempt()
        message = self.client.messages.create(
            model=self.model,
            max_tokens=3000,
            messages=[{"role": "user", "content": [doc_block, {"type": "text", "text": prompt}]}],
        )
        return "".join(b.text for b in message.content if b.type == "text")

    def complete(self, prompt: str, on_attempt=None) -> str:
        if on_attempt is not None:
            on_attempt()
        message = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in message.content if b.type == "text")


class GeminiClient:
    provider = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash"):
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)

    def extract(self, file_bytes: bytes, media_type: str, prompt: str, on_attempt=None) -> str:
        content = [{"mime_type": media_type, "data": file_bytes}, prompt]

        def call():
            response = self._model.generate_content(content, request_options={"timeout": 120})
            return response.text

        return _retry_call(call, on_attempt=on_attempt)

    def complete(self, prompt: str, on_attempt=None) -> str:
        def call():
            response = self._model.generate_content(prompt, request_options={"timeout": 90})
            return response.text

        return _retry_call(call, on_attempt=on_attempt)


def get_default_client():
    """Returns the AI client configured by DEFAULT_AI_PROVIDER in .env.
    (A future improvement: let each Partner choose their own provider —
    not needed yet at Phase 0/1 scale.)"""
    if DEFAULT_AI_PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            raise RuntimeError("DEFAULT_AI_PROVIDER is 'gemini' but GEMINI_API_KEY is not set.")
        return GeminiClient(GEMINI_API_KEY)
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("DEFAULT_AI_PROVIDER is 'claude' but ANTHROPIC_API_KEY is not set.")
    return ClaudeClient(ANTHROPIC_API_KEY)
