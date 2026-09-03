"""
Unified AI client wrapper.

Both document_reader.py and company_brain.py talk to this single interface
(.extract(...) and .chat(...)) instead of calling a specific vendor's SDK
directly. That's what lets app.py swap between Claude and Gemini with a
sidebar toggle without touching the rest of the code.
"""

import base64


class ClaudeClient:
    """Wraps Anthropic's Claude API behind the shared .extract() / .chat() interface."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-5"):
        from anthropic import Anthropic

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def extract(self, file_bytes: bytes, media_type: str, is_pdf: bool, prompt: str) -> str:
        b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
        doc_block = {
            "type": "document" if is_pdf else "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }
        message = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{"role": "user", "content": [doc_block, {"type": "text", "text": prompt}]}],
        )
        return "".join(b.text for b in message.content if b.type == "text")

    def chat(self, prompt: str) -> str:
        message = self.client.messages.create(
            model=self.model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in message.content if b.type == "text")


class GeminiClient:
    """Wraps Google's free-tier Gemini API behind the shared .extract() / .chat() interface."""

    REQUEST_TIMEOUT_SECONDS = 45

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash"):
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)

    def extract(self, file_bytes: bytes, media_type: str, is_pdf: bool, prompt: str) -> str:
        response = self._model.generate_content(
            [{"mime_type": media_type, "data": file_bytes}, prompt],
            request_options={"timeout": self.REQUEST_TIMEOUT_SECONDS},
        )
        return response.text

    def chat(self, prompt: str) -> str:
        response = self._model.generate_content(
            prompt,
            request_options={"timeout": self.REQUEST_TIMEOUT_SECONDS},
        )
        return response.text


def get_client(provider: str, api_key: str):
    """provider is 'claude' or 'gemini'. Returns None if no key was given."""
    if not api_key:
        return None
    if provider == "gemini":
        return GeminiClient(api_key)
    return ClaudeClient(api_key)
