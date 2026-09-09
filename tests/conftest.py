"""
Shared test fixtures.

Isolation strategy:
  * There is NO real Supabase / Gemini / HTTP in these tests. Importing the
    app normally would build an engine from the repo .env (production DB).
    Instead we set env vars BEFORE any app import, then swap the FastAPI
    get_db dependency and every module-global SessionLocal for an in-memory
    SQLite engine. The background batch workers are disabled via
    HHX_DISABLE_BACKGROUND_WORKERS so nothing talks to the outside world.
  * The AI client (get_default_client) is replaced with a fake whose
    .extract() returns a canned JSON result (see CANNED dicts), so the full
    extract -> review -> approve -> webhook path runs against real code paths.
  * deliver_webhook (imported by-name into main/review/hitl/batch) is patched
    to a recorder, so no network call is made while still asserting the
    payload that WOULD have been delivered.
"""

import json
import os
import tempfile

# Must be set BEFORE importing any app module.
os.environ["HHX_DISABLE_BACKGROUND_WORKERS"] = "1"
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="hhx-test-uploads-")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.ai_client as ai_mod
import app.batch as batch_mod
import app.db as db_mod
import app.main as main_mod
import app.review as review_mod
import app.hitl as hitl_mod
import app.search as search_mod
import app.health as health_mod
from app.db import get_db
from app.models import Base, Partner


# ---------------------------------------------------------------------------
# Canned AI extraction outputs. HIGH_CONF is the clean happy-path result;
# LOW_CONF drops total_amount.confidence below the 0.85 review threshold so
# the review-queue flagging (needs_review) can be proven end to end.
# ---------------------------------------------------------------------------
HIGH_CONF = {
    "invoice_count": 1,
    "invoices": [
        {
            "document_type": "gst_invoice",
            "vendor": {"name": "Sharma Traders", "gstin": "27AAACS1234F1Z5", "confidence": 0.99},
            "invoice_details": {
                "invoice_number": "INV-2026-0142",
                "date": "2026-08-12",
                "confidence": 0.98,
            },
            "line_items": [
                {
                    "description": "Steel rods 10mm",
                    "hsn_sac_code": "7214",
                    "quantity": 100,
                    "unit_price": 65.0,
                    "tax_cgst": 585.0,
                    "tax_sgst": 585.0,
                    "tax_igst": 0.0,
                    "total_value": 7670.0,
                    "confidence": 0.99,
                }
            ],
            "total_amount": {"value": 7670.0, "confidence": 0.99},
            "flags": [],
        }
    ],
    "document_flags": [],
}

LOW_CONF = json.loads(json.dumps(HIGH_CONF))
LOW_CONF["invoices"][0]["total_amount"]["confidence"] = 0.31

# The fake client reads its output from this dict so a test can switch what
# "the model said" mid-test (e.g. extract low-confidence then approve high).
CANNED = {"text": json.dumps(HIGH_CONF)}


class _FakeAI:
    provider = "gemini"

    def __init__(self, result_text: str):
        self._text = result_text

    def extract(self, file_bytes, media_type, prompt):
        return self._text

    def complete(self, prompt):
        return json.dumps({"answer": "canned", "sources": []})


def _fake_client():
    return _FakeAI(CANNED["text"])


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Swap DB sessions, the AI client, and webhook delivery for test fakes."""
    _DELIVERY_LOG.clear()
    CANNED["text"] = json.dumps(HIGH_CONF)  # each test starts from the clean result
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(db_mod, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(batch_mod, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(health_mod, "SessionLocal", TestingSessionLocal)

    monkeypatch.setattr(ai_mod, "get_default_client", _fake_client)
    monkeypatch.setattr(main_mod, "get_default_client", _fake_client)
    monkeypatch.setattr(batch_mod, "get_default_client", _fake_client)
    monkeypatch.setattr(search_mod, "get_default_client", _fake_client)

    def _record_delivery(url, payload):
        _DELIVERY_LOG.append({"url": url, "payload": payload})
        return True

    # main/review/hitl bind deliver_webhook at module import; batch resolves
    # it lazily from app.webhooks inside the worker. Patch both surfaces.
    import app.webhooks as webhooks_mod

    monkeypatch.setattr(webhooks_mod, "deliver_webhook", _record_delivery)
    monkeypatch.setattr(main_mod, "deliver_webhook", _record_delivery)
    monkeypatch.setattr(review_mod, "deliver_webhook", _record_delivery)
    monkeypatch.setattr(hitl_mod, "deliver_webhook", _record_delivery)

    main_mod.app.dependency_overrides[get_db] = override_get_db

    yield

    _DELIVERY_LOG.clear()
    main_mod.app.dependency_overrides.clear()


# Module-level recorder so the autouse fixture (runs first) and the
# delivery_log fixture (requested by individual tests) share the same list.
_DELIVERY_LOG: list = []


@pytest.fixture()
def delivery_log():
    return _DELIVERY_LOG


@pytest.fixture()
def set_canned():
    def _set(result):
        CANNED["text"] = json.dumps(result)

    return _set


@pytest.fixture()
def canned():
    """Expose the canned extraction dicts to test modules."""
    return {"high": HIGH_CONF, "low": LOW_CONF}


@pytest.fixture()
def pdf_bytes():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "benchmark",
        "pdfs",
        "invoice_deccan_hardware.pdf",
    )
    with open(path, "rb") as fh:
        return fh.read()


@pytest.fixture()
def test_key():
    return "test-api-key-0000abcd"


@pytest.fixture()
def seed_partner(test_key, _no_real_network):
    """Insert a Partner + return its id. The explicit dependency on
    _no_real_network guarantees the SQLite override is active first."""
    from app.models import Partner as P

    db = db_mod.SessionLocal()
    try:
        partner = P(name="SmokeTest Co", api_key=test_key)
        db.add(partner)
        db.commit()
        db.refresh(partner)
        return str(partner.id)
    finally:
        db.close()


@pytest.fixture()
def client():
    with TestClient(main_mod.app) as c:
        yield c