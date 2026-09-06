"""
Same provider-abstraction pattern as the Streamlit app's ai_client.py —
kept independent here since this backend is a separate service, but the
interface (.extract()) is identical on purpose so extraction logic reads
the same way in both places.
"""

import base64
import time

from app.config import ANTHROPIC_API_KEY, GEMINI_API_KEY, DEFAULT_AI_PROVIDER


def _is_rate_limited(exc: Exception) -> bool:
    """True if the error looks like a provider throttle/quota limit (429),
    as opposed to a hard error (bad key, invalid PDF, etc)."""
    msg = str(exc)
    return "429" in msg or "quota" in msg.lower() or "rate" in msg.lower() or "RESOURCE_EXHAUSTED" in msg


def _is_daily_quota(exc: Exception) -> bool:
    """True if the 429 is a per-DAY free-tier cap (which no amount of
    waiting/retrying will fix — the quota resets at midnight)."""
    msg = str(exc)
    return "PerDayPerProject" in msg or "free_tier_requests" in msg


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    if _is_daily_quota(exc):
        return "Daily provider quota exceeded (free tier allows only 20 requests/day). Retry tomorrow or upgrade the API key."
    if "429" in msg:
        return "Provider rate limit hit (429). Try again in a few minutes."
    return msg


def _retry_call(fn, *, attempts: int = 5, base_delay: float = 2.0):
    """Call fn() with exponential backoff + jitter, retrying transient rate
    limits. Fails fast on hard errors and per-DAY quota caps (retrying those
    wastes time). Raises the last error if it never succeeds."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - we re-raise at the end
            last_exc = exc
            if attempt >= attempts - 1 or not _is_rate_limited(exc) or _is_daily_quota(exc):
                raise
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

    def extract(self, file_bytes: bytes, media_type: str, prompt: str) -> str:
        b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
        doc_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }
        message = self.client.messages.create(
            model=self.model,
            max_tokens=3000,
            messages=[{"role": "user", "content": [doc_block, {"type": "text", "text": prompt}]}],
        )
        return "".join(b.text for b in message.content if b.type == "text")

    def complete(self, prompt: str) -> str:
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

    def extract(self, file_bytes: bytes, media_type: str, prompt: str) -> str:
        content = [{"mime_type": media_type, "data": file_bytes}, prompt]

        def call():
            response = self._model.generate_content(content, request_options={"timeout": 60})
            return response.text

        return _retry_call(call)

    def complete(self, prompt: str) -> str:
        def call():
            response = self._model.generate_content(prompt, request_options={"timeout": 90})
            return response.text

        return _retry_call(call)


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
