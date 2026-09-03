"""Liveness / readiness probes + a lightweight Prometheus-format metrics 
endpoint.

Dependency-free by design (no prometheus client): we keep simple in-process
counters and render the text format ourselves so /metrics works with any
scraper (Render, Cloudflare, Grafana, VictoriaMetrics, prometheus).

Endpoints:
  GET /healthz  -> 200 if this process is alive (no DB dependency).
  GET /readyz   -> 200 when it can serve traffic (DB reachable), else 503.
                   This is what the load balancer / docker healthcheck uses.
  GET /metrics  -> Prometheus text exposition of the counters below.
"""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter
from sqlalchemy import text

from app.db import SessionLocal

router = APIRouter()

STARTED_AT = time.monotonic()

# --- In-process counters (request_level.inc(method, route, status, provider)) ---
_lock = threading.Lock()
_total = 0
_by_method = {}
_by_status = {}
_by_route = {}
_documents_total = 0
_documents_failed = 0
_webhooks_ok = 0
_webhooks_failed = 0
_duration_sum_ms = 0.0


def record_request(method: str, route: str, status: int, duration_ms: float,
                   provider: str = "") -> None:
    global _total, _duration_sum_ms
    with _lock:
        _total += 1
        _duration_sum_ms += duration_ms
        _by_method[method] = _by_method.get(method, 0) + 1
        _by_status[str(status)] = _by_status.get(str(status), 0) + 1
        _by_route[route] = _by_route.get(route, 0) + 1


def record_document(ok: bool) -> None:
    global _documents_total, _documents_failed
    with _lock:
        _documents_total += 1
        if not ok:
            _documents_failed += 1


def record_webhook(ok: bool) -> None:
    global _webhooks_ok, _webhooks_failed
    with _lock:
        if ok:
            _webhooks_ok += 1
        else:
            _webhooks_failed += 1


@router.get("/healthz")
def healthz():
    return {"status": "ok", "uptime_s": int(time.monotonic() - STARTED_AT)}


@router.get("/readyz")
def readyz():
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception:
        import app.health as h  # avoid circular import surprises
        from app.logging_setup import get_logger
        get_logger("health").warning("readyz: DB check failed",
                                     extra={"component": "readiness"})
        return {
            "status": "unavailable",
            "checks": {"db": "down"},
        }, 503
    return {"status": "ok", "checks": {"db": "up"}}


@router.get("/metrics")
def metrics():
    with _lock:
        def c(name, value):
            return f"# TYPE {name} counter\n{name} {value}"

        lines = [
            "# HELP halohubx_http_requests_total Total HTTP requests served.",
            c("halohubx_http_requests_total", _total),
            "# HELP halohubx_http_request_duration_ms_sum Total request duration (ms).",
            c("halohubx_http_request_duration_ms_sum", round(_duration_sum_ms, 3)),
            "# HELP halohubx_http_requests_by_method Requests by HTTP method.",
            c("halohubx_http_requests_by_method", sum(_by_method.values())),
            "# HELP halohubx_http_requests_by_status Requests by HTTP status code.",
            c("halohubx_http_requests_by_status", sum(_by_status.values())),
            "# HELP halohubx_http_requests_by_route Requests by route.",
            c("halohubx_http_requests_by_route", sum(_by_route.values())),
            "# HELP halohubx_documents_processed_total Documents processed (upload/confirm).",
            c("halohubx_documents_processed_total", _documents_total),
            "# HELP halohubx_documents_failed_total Documents that failed processing.",
            c("halohubx_documents_failed_total", _documents_failed),
            "# HELP halohubx_webhooks_delivered_total Webhook deliveries.",
            c("halohubx_webhooks_delivered_total", _webhooks_ok),
            "# HELP halohubx_webhooks_failed_total Failed webhook deliveries.",
            c("halohubx_webhooks_failed_total", _webhooks_failed),
        ]
        for label, buckets in (("method", _by_method), ("status", _by_status),
                               ("route", _by_route)):
            for key, val in sorted(buckets.items()):
                lines.append(
                    f"halohubx_http_requests_total{{route_type=\"{label}\", "
                    f"label=\"{key}\"}} {val}"
                )
    return "\n".join(lines)
