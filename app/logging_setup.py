"""Structured (JSON) logging for the API and all app modules.

One logger, one formatter, driven by environment config in app/config.py:

  LOG_JSON=1        -> emit one JSON object per line (default in containers)
  LOG_LEVEL=<name>  -> "INFO" (default), "DEBUG", "WARNING", "ERROR"

Why JSON: it is greppable, ships straight to Render/Cloudflare/ELK-style
ingestors, and every structured field (document_id, partner, provider,
duration_ms, status) becomes a queryable dimension instead of buried in text.
"""

from __future__ import annotations

import json
import logging
import sys
import time

from app.config import LOG_JSON, LOG_LEVEL


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single JSON object per line.

    Always includes timestamp, level, logger, and message. Any keyword fields
    passed via logger.info("...", extra={"document_id": ...}) are merged at
    the top level so they become first-class queryable keys. Stdlib LogRecord
    internal attributes are excluded — only deliberate `extra={...}` keys
    (plus any non-standard attribute a user sets) survive.
    """

    _STDLIB_ATTRS = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    })

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge only deliberate fields, not stdlib LogRecord internals.
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in self._STDLIB_ATTRS:
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-friendly fallback used when LOG_JSON is false (local dev)."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [f"{self.formatTime(record, '%H:%M:%S')}",
                 record.levelname.ljust(7), record.getMessage()]
        extra = [
            f"{k}={v}"
            for k, v in record.__dict__.items()
            if not k.startswith("_")
            and k not in ("name", "msg", "args", "levelname", "levelno",
                          "pathname", "filename", "module", "exc_info",
                          "exc_text", "stack_info", "lineno", "funcName",
                          "created", "msecs", "relativeCreated", "thread",
                          "threadName", "processName", "process", "message",
                          "asctime", "taskName")
        ]
        if extra:
            parts.extend(extra)
        if record.exc_info:
            parts.append(self.formatException(record.exc_info))
        return " ".join(parts)


def setup_logging() -> None:
    """Install the root handler/formatter once (idempotent)."""
    root = logging.getLogger()
    # Remove uvicorn's own handlers so we own the stream (avoids duplicate lines).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if LOG_JSON else TextFormatter())

    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    root.addHandler(handler)

    # Quiet noisy third-party loggers that would spam structured output.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return an app logger ready for structured extras."""
    return logging.getLogger(f"halohubx.{name}")


def elapsed_ms(start: float) -> float:
    """Milliseconds since a monotonic start timestamp (for request timing)."""
    return round((time.monotonic() - start) * 1000, 1)
