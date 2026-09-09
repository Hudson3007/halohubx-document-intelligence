"""
Smoke tests for the core document flow:

    upload/extract -> review (low-confidence flag) -> approve/confirm -> webhook

Everything runs against the fakes in conftest (SQLite, canned AI output,
webhook recorder). No network, no prod database, no Gemini.

Run:  python -m pytest tests/ -v
"""

import time

import pytest


def _headers(test_key):
    return {"Authorization": f"Bearer {test_key}"}


def _upload(client, pdf_bytes, test_key, client_name="Acme Co", webhook_url=None):
    """POST /upload the fixture PDF and return the parsed response."""
    data = {"client_name": client_name}
    if webhook_url:
        data["webhook_url"] = webhook_url
    resp = client.post(
        "/upload",
        files={
            "file": ("invoice_deccan_hardware.pdf", pdf_bytes, "application/pdf")
        },
        data=data,
        headers=_headers(test_key),
    )
    return resp


class TestHealth:
    def test_healthz(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_readyz(self, client, _no_real_network):
        r = client.get("/readyz")
        assert r.status_code == 200
        assert r.json()["checks"]["db"] == "up"


class TestAuth:
    def test_upload_requires_token(self, client, pdf_bytes):
        r = client.post(
            "/upload",
            files={"file": ("x.pdf", pdf_bytes, "application/pdf")},
            data={"client_name": "Acme Co"},
        )
        assert r.status_code == 401

    def test_bad_token_rejected(self, client, pdf_bytes):
        r = client.post(
            "/upload",
            files={"file": ("x.pdf", pdf_bytes, "application/pdf")},
            data={"client_name": "Acme Co"},
            headers={"Authorization": "Bearer nope-not-a-real-key"},
        )
        assert r.status_code == 401


class TestUpload:
    def test_rejects_non_pdf(self, client, test_key, seed_partner):
        r = client.post(
            "/upload",
            files={"file": ("fake.pdf", b"this is not a pdf at all", "application/pdf")},
            data={"client_name": "Acme Co"},
            headers=_headers(test_key),
        )
        assert r.status_code == 400

    def test_upload_completes_sync(self, client, pdf_bytes, test_key, seed_partner):
        r = _upload(client, pdf_bytes, test_key)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "completed"
        assert body["result"]["invoice_count"] == 1
        assert body["result"]["invoices"][0]["vendor"]["name"] == "Sharma Traders"

    def test_status_and_retrieve(self, client, pdf_bytes, test_key, seed_partner):
        doc_id = _upload(client, pdf_bytes, test_key).json()["document_id"]

        st = client.get(f"/status/{doc_id}", headers=_headers(test_key))
        assert st.status_code == 200
        assert st.json()["status"] == "completed"

        rv = client.get(f"/retrieve/{doc_id}", headers=_headers(test_key))
        assert rv.status_code == 200
        assert rv.json()["result"]["invoice_count"] == 1

    def test_not_found(self, client, test_key, seed_partner):
        r = client.get("/status/does-not-exist", headers=_headers(test_key))
        assert r.status_code == 404


class TestReviewFlow:
    def test_low_confidence_flags_for_review(
        self,
        client,
        pdf_bytes,
        test_key,
        seed_partner,
        set_canned,
        canned,
        delivery_log,
    ):
        set_canned(canned["low"])
        doc_id = _upload(client, pdf_bytes, test_key).json()["document_id"]

        # The document should appear in the HITL review queue.
        docs = client.get(
            "/documents?review_status=needs_review", headers=_headers(test_key)
        ).json()
        assert [d["document_id"] for d in docs] == [doc_id]
        assert docs[0]["needs_review"] is True

        # Review detail exposes the original PDF for the side-by-side preview.
        det = client.get(f"/documents/{doc_id}/review", headers=_headers(test_key))
        assert det.status_code == 200
        body = det.json()
        assert body["has_file"] is True
        assert body["result"]["invoices"][0]["total_amount"]["confidence"] == 0.31

        # The stored file endpoint serves real PDF bytes back.
        fl = client.get(f"/documents/{doc_id}/file", headers=_headers(test_key))
        assert fl.status_code == 200
        assert fl.headers["content-type"] == "application/pdf"
        assert fl.content.startswith(b"%PDF-")

    def test_approve_delivers_webhook_and_clears_queue(
        self,
        client,
        pdf_bytes,
        test_key,
        seed_partner,
        set_canned,
        canned,
        delivery_log,
    ):
        set_canned(canned["low"])
        doc_id = _upload(
            client,
            pdf_bytes,
            test_key,
            client_name="Acme Co",
            webhook_url="https://erp.example.com/hook",
        ).json()["document_id"]

        # A human corrects the low-confidence total and approves.
        corrected = {
            "invoice_count": 1,
            "invoices": [
                {
                    **canned["low"]["invoices"][0],
                    "total_amount": {"value": 7800.0, "confidence": 0.99},
                }
            ],
            "document_flags": [],
        }
        r = client.post(
            f"/documents/{doc_id}/approve",
            json={"result": corrected},
            headers=_headers(test_key),
        )
        assert r.status_code == 200, r.text
        assert r.json()["webhook_delivered"] is True

        # Webhook payload carries the corrected result to the partner ERP.
        assert len(delivery_log) == 1
        payload = delivery_log[0]["payload"]
        assert payload["document_id"] == doc_id
        assert payload["status"] == "completed"
        assert payload["result"]["invoices"][0]["total_amount"]["value"] == 7800.0

        # Approved docs drop out of the needs_review queue.
        docs = client.get(
            "/documents?review_status=needs_review", headers=_headers(test_key)
        ).json()
        assert docs == []

        # And the approval is on the audit trail.
        audit = client.get("/audit", headers=_headers(test_key)).json()["entries"]
        assert any(
            e["action"] == "approval" and e["document_id"] == doc_id for e in audit
        )

    def test_hitl_confirm_path(
        self,
        client,
        pdf_bytes,
        test_key,
        seed_partner,
        set_canned,
        canned,
        delivery_log,
    ):
        """The frontend confirm flow (POST /hitl/document/{id}/confirm)."""
        set_canned(canned["low"])
        doc_id = _upload(
            client,
            pdf_bytes,
            test_key,
            webhook_url="https://erp.example.com/hook",
        ).json()["document_id"]

        r = client.post(
            f"/hitl/document/{doc_id}/confirm",
            json={"result": canned["high"]},
            headers=_headers(test_key),
        )
        assert r.status_code == 200, r.text
        assert r.json()["webhook_delivered"] is True
        assert delivery_log[-1]["payload"]["result"]["invoice_count"] == 1

        audit = client.get("/audit", headers=_headers(test_key)).json()["entries"]
        assert any(
            e["action"] == "review_edit" and e["document_id"] == doc_id for e in audit
        )


class TestUsageAndQuota:
    def test_usage_endpoint(self, client, test_key, seed_partner):
        r = client.get("/usage", headers=_headers(test_key))
        assert r.status_code == 200
        assert r.json()["monthly_quota"] == 1000
        assert r.json()["can_extract"] is True

    def test_ai_usage_meter(self, client, test_key, seed_partner):
        r = client.get("/usage/ai", headers=_headers(test_key))
        assert r.status_code == 200
        assert r.json()["provider"] == "gemini"
        assert r.json()["daily_limit"] == 20
        assert r.json()["remaining"] == 20

    def test_quota_exhausted_blocks_upload(
        self, client, pdf_bytes, test_key, seed_partner
    ):
        # Pin today's meter at the ceiling.
        resp = client.post(
            "/usage/ai/reset", json={"used": 20}, headers=_headers(test_key)
        )
        assert resp.status_code == 200

        r = _upload(client, pdf_bytes, test_key)
        assert r.status_code == 429
        assert r.json()["detail"]["error"] == "ai_quota_exhausted"

        # Calibrate back down and the upload succeeds again.
        client.post("/usage/ai/reset", json={"used": 0}, headers=_headers(test_key))
        assert _upload(client, pdf_bytes, test_key).status_code == 200

    def test_insufficient_credits_402(self, client, pdf_bytes, test_key):
        from app.db import SessionLocal
        from app.models import Partner

        db = SessionLocal()
        try:
            broke = Partner(name="Broke Co", api_key="test-api-key-no-credits")
            broke.monthly_credit_quota = 0
            db.add(broke)
            db.commit()
        finally:
            db.close()

        r = _upload(
            client, pdf_bytes, "test-api-key-no-credits", client_name="Acme Co"
        )
        assert r.status_code == 402
        assert r.json()["detail"]["error"] == "insufficient_credits"


class TestBatch:
    def test_batch_upload_processes_and_delivers(
        self, client, pdf_bytes, test_key, seed_partner, delivery_log
    ):
        r = client.post(
            "/upload/batch",
            files=[
                ("files", ("bill_a.pdf", pdf_bytes, "application/pdf")),
                ("files", ("bill_b.pdf", pdf_bytes, "application/pdf")),
            ],
            data={"client_name": "Acme Co", "webhook_url": "https://erp.example.com/hook"},
            headers=_headers(test_key),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["submitted"] == 2
        assert body["rejected"] == []
        doc_ids = [d["document_id"] for d in body["documents"]]

        # Background extraction is async; poll status AND webhook delivery.
        deadline = time.time() + 10
        statuses = []
        while time.time() < deadline:
            st = client.post(
                "/status/batch",
                json={"document_ids": doc_ids},
                headers=_headers(test_key),
            ).json()
            statuses = [d["status"] for d in st["documents"]]
            if all(s == "completed" for s in statuses) and len(delivery_log) >= 2:
                break
            time.sleep(0.3)

        assert all(s == "completed" for s in statuses), statuses
        assert len(delivery_log) == 2

    def test_batch_rejects_bad_files_but_keeps_good(
        self, client, pdf_bytes, test_key, seed_partner
    ):
        r = client.post(
            "/upload/batch",
            files=[
                ("files", ("good.pdf", pdf_bytes, "application/pdf")),
                ("files", ("bad.txt", b"not a pdf", "text/plain")),
            ],
            data={"client_name": "Acme Co"},
            headers=_headers(test_key),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["submitted"] == 1
        assert len(body["rejected"]) == 1
        assert body["rejected"][0]["filename"] == "bad.txt"


class TestAudit:
    def test_upload_is_audited(self, client, pdf_bytes, test_key, seed_partner):
        doc_id = _upload(client, pdf_bytes, test_key).json()["document_id"]
        audit = client.get("/audit", headers=_headers(test_key)).json()["entries"]
        assert any(e["action"] == "upload" and e["document_id"] == doc_id for e in audit)