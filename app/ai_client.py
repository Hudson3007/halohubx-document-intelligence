"""
Same provider-abstraction pattern as the Streamlit app's ai_client.py —
kept independent here since this backend is a separate service, but the
interface (.extract()) is identical on purpose so extraction logic reads
the same way in both places.
"""

import base64

from app.config import ANTHROPIC_API_KEY, GEMINI_API_KEY, DEFAULT_AI_PROVIDER


class ClaudeClient:
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


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-3.5-flash"):
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)

    def extract(self, file_bytes: bytes, media_type: str, prompt: str) -> str:
        response = self._model.generate_content(
            [{"mime_type": media_type, "data": file_bytes}, prompt],
            request_options={"timeout": 60},
        )
        return response.text


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
