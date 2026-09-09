"""
Unit tests for the webhook SSRF guard + delivery retry loop (pure logic, no DB).

Run:  python -m pytest tests/test_ssrf.py -v
"""

import ipaddress

import pytest

import app.webhooks as w

# The conftest autouse fixture swaps app.webhooks.deliver_webhook for a
# recorder; capture the REAL implementation here (module import runs before
# any fixture setup) so these unit tests exercise the actual retry logic.
_REAL_DELIVER = w.deliver_webhook


class TestValidateWebhookUrl:
    def test_accepts_https_public_host(self, monkeypatch):
        from app import ssrf

        def _resolve(hostname):
            return [ipaddress.ip_address("93.184.216.34")]

        monkeypatch.setattr(ssrf, "_host_ipv4_and_v6", _resolve)
        assert ssrf.validate_webhook_url("https://erp.example.com/hook") == "https://erp.example.com/hook"

    def test_rejects_loopback_localhost(self):
        from app.ssrf import validate_webhook_url

        with pytest.raises(ValueError):
            validate_webhook_url("https://localhost/webhook")
        with pytest.raises(ValueError):
            validate_webhook_url("https://127.0.0.1/webhook")

    def test_rejects_private_ip(self):
        from app.ssrf import validate_webhook_url

        with pytest.raises(ValueError):
            validate_webhook_url("https://192.168.1.10/hook")
        with pytest.raises(ValueError):
            validate_webhook_url("https://10.0.0.5/hook")

    def test_rejects_http_to_public_host(self):
        from app.ssrf import validate_webhook_url

        with pytest.raises(ValueError):
            validate_webhook_url("http://erp.example.com/hook")

    def test_rejects_missing_scheme(self, monkeypatch):
        from app import ssrf
        monkeypatch.setattr(ssrf, "ALLOW_HTTP_LOOPBACK_WEBHOOKS", True)

        with pytest.raises(ValueError):
            ssrf.validate_webhook_url("erp.example.com/hook")


class TestDeliverWebhook:
    def test_delivers_and_returns_true_on_2xx(self, monkeypatch):
        import app.webhooks as w

        captured = {}

        class FakeResponse:
            status_code = 200

        def fake_post(url, json, timeout):
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse()

        monkeypatch.setattr(w, "httpx", type("X", (), {"post": staticmethod(fake_post)}))
        monkeypatch.setattr(w, "validate_webhook_url", lambda u: u)

        ok = _REAL_DELIVER("https://erp.example.com/hook", {"a": 1})
        assert ok is True
        assert captured["url"] == "https://erp.example.com/hook"
        assert captured["payload"] == {"a": 1}

    def test_retries_then_returns_false_on_persistent_failure(self, monkeypatch):
        import app.webhooks as w

        calls = []

        def fake_post(url, json, timeout):
            calls.append(url)
            raise ConnectionError("down")

        monkeypatch.setattr(w, "httpx", type("X", (), {"post": staticmethod(fake_post)}))
        monkeypatch.setattr(w, "validate_webhook_url", lambda u: u)
        monkeypatch.setattr(w, "WEBHOOK_MAX_RETRIES", 3)
        monkeypatch.setattr(w.time, "sleep", lambda s: None)  # keep the test fast

        ok = _REAL_DELIVER("https://erp.example.com/hook", {"a": 1})
        assert ok is False
        assert len(calls) == 3

    def test_unsafe_url_returns_false_without_calling(self, monkeypatch):
        import app.webhooks as w

        called = {"n": 0}

        def fake_post(*a, **k):
            called["n"] += 1
            return type("R", (), {"status_code": 200})()

        monkeypatch.setattr(w, "httpx", type("X", (), {"post": staticmethod(fake_post)}))
        monkeypatch.setattr(w, "validate_webhook_url", lambda u: (_ for _ in ()).throw(ValueError("loopback")))

        ok = _REAL_DELIVER("https://localhost/hook", {"a": 1})
        assert ok is False
        assert called["n"] == 0