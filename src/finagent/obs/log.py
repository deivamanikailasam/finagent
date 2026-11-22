"""JSON logging with run-ID correlation. Every record passes through redact()."""

import hashlib
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TextIO

from finagent.config.clock import Clock, SystemClock
from finagent.obs.redaction import redact

_run_id: ContextVar[str | None] = ContextVar("finagent_run_id", default=None)
_DROPPED_RECORD = '{"level": "ERROR", "event": "log_record_dropped"}'
_SCALARS = (str, int, float, bool, Decimal, date, datetime, type(None))

logger = logging.getLogger("finagent")
logger.addHandler(logging.NullHandler())
logger.propagate = False  # root handlers would bypass the redacting formatter


@contextmanager
def run_context(run_id: str) -> Iterator[None]:
    token = _run_id.set(run_id)
    try:
        yield
    finally:
        _run_id.reset(token)


def _is_id_key(key: str) -> bool:
    is_id = key == "id" or key.endswith(("_id", "_ref"))
    return is_id and "account" not in key  # an account identifier is payment data, not a record ID


def summarise_payload(payload: Any) -> dict[str, Any]:
    """Reduce a payload to its hash plus top-level IDs, counts and status."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    summary: dict[str, Any] = {"payload_hash": hashlib.sha256(canonical.encode()).hexdigest()}
    if isinstance(payload, list | tuple):
        summary["count"] = len(payload)
    if isinstance(payload, dict):
        for key, value in payload.items():
            name = str(key)
            if isinstance(value, list | tuple | dict):
                summary[f"{name}_count"] = len(value)
            elif isinstance(value, str | int) and (name == "status" or _is_id_key(name)):
                summary[name] = value
    return summary


def _minimum_necessary(value: Any) -> Any:
    # Anything structured could hold line items or payment details, so only its summary is logged.
    if isinstance(value, _SCALARS):
        return value
    try:
        return summarise_payload(value)
    except Exception:
        return {"summary_unavailable": True}


class JsonFormatter(logging.Formatter):
    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self._clock = clock

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "finagent_fields", {})
        entry = {
            **fields,
            "ts": self._clock.now_utc().isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "run_id": _run_id.get(),
            "outcome": getattr(record, "finagent_outcome", None),
        }
        duration_ms = getattr(record, "finagent_duration_ms", None)
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms
        if record.exc_info and record.exc_info[0] is not None:
            # Type only: exception text and tracebacks can quote payload values.
            entry["error_type"] = record.exc_info[0].__name__
        return json.dumps(redact(entry), default=str)


class _SafeStreamHandler(logging.StreamHandler):
    """Replaces the stdlib error report, which would print the raw message and arguments."""

    def handleError(self, record: logging.LogRecord | None) -> None:  # noqa: N802
        try:
            self.stream.write(_DROPPED_RECORD + self.terminator)
            self.flush()
        except Exception:
            pass  # logging must never break a run


def _emit_dropped() -> None:
    for handler in logger.handlers:
        if isinstance(handler, _SafeStreamHandler):
            handler.handleError(None)


def configure_logging(
    stream: TextIO | None = None, clock: Clock | None = None, level: int = logging.INFO
) -> None:
    """Send JSON records to stderr by default, keeping stdout clean for CLI output."""
    handler = _SafeStreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter(clock or SystemClock()))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)


def log_event(
    event: str,
    *,
    outcome: str | None = None,
    duration_ms: int | float | None = None,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    try:
        safe_fields = {key: _minimum_necessary(value) for key, value in fields.items()}
        logger.log(
            level,
            "%s",
            event,
            extra={
                "finagent_fields": safe_fields,
                "finagent_outcome": outcome,
                "finagent_duration_ms": duration_ms,
            },
        )
    except Exception:
        _emit_dropped()  # a logging problem must not change the outcome of a run
