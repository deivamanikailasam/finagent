import hashlib
import io
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from finagent.config.clock import FixedClock
from finagent.obs.log import configure_logging, log_event, logger, run_context

FAKE_API_KEY = "sk-" + "ant-" + "placeholder-value-123"


@pytest.fixture
def stream():
    handlers, level = list(logger.handlers), logger.level
    output = io.StringIO()
    configure_logging(output, FixedClock(datetime(2026, 9, 15, 0, 0, tzinfo=UTC)))
    yield output
    logger.handlers[:] = handlers
    logger.setLevel(level)


def records(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_record_has_required_fields(stream):
    log_event("tool.completed", outcome="ok", duration_ms=12)
    record = records(stream)[0]
    assert record["ts"] == "2026-09-15T00:00:00+00:00"
    assert record["level"] == "INFO"
    assert record["event"] == "tool.completed"
    assert record["run_id"] is None
    assert record["outcome"] == "ok"
    assert record["duration_ms"] == 12


def test_duration_is_omitted_when_not_given(stream):
    log_event("run.started")
    assert "duration_ms" not in records(stream)[0]


def test_run_id_correlation_and_reset(stream):
    with run_context("run-1"):
        log_event("inside")
        with run_context("run-2"):
            log_event("nested")
        log_event("restored")
    log_event("outside")
    assert [record["run_id"] for record in records(stream)] == ["run-1", "run-2", "run-1", None]


def test_no_secret_in_output(stream):
    log_event(f"failed for account 12345678 with {FAKE_API_KEY}", contact="ap.clerk@example.com")
    output = stream.getvalue()
    assert "12345678" not in output
    assert FAKE_API_KEY not in output
    assert "ap.clerk@example.com" not in output
    assert "****5678" in output


def test_plain_stdlib_records_are_redacted(stream):
    logging.getLogger("finagent.tools").warning("bad account %s", "12345678")
    assert "12345678" not in stream.getvalue()


def test_exception_text_is_not_logged(stream):
    try:
        raise ValueError("account 12345678 secret detail")
    except ValueError:
        logger.exception("tool.failed")
    record = records(stream)[0]
    assert record["error_type"] == "ValueError"
    assert "secret detail" not in stream.getvalue()


def test_full_payload_keeps_only_ids_counts_status_and_hash(stream):
    payload = {
        "invoice_id": "INV-1001",
        "posting_ref": "POST-1",
        "status": "APPROVED",
        "account_id": "12345678",
        "supplier_note": "private supplier wording",
        "remit_to": {"account": "12345678", "bsb": "062000"},
        "lines": [{"description": "private line item", "unit_price": "150.00"}],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    log_event("tool.result", payload=payload)
    assert records(stream)[0]["payload"] == {
        "payload_hash": hashlib.sha256(canonical.encode()).hexdigest(),
        "invoice_id": "INV-1001",
        "posting_ref": "POST-1",
        "status": "APPROVED",
        "remit_to_count": 2,
        "lines_count": 1,
    }
    assert "private" not in stream.getvalue()


def test_object_field_is_reduced_to_a_hash(stream):
    class Record:
        def __str__(self):
            return "private object content"

    log_event("tool.result", payload=Record())
    assert set(records(stream)[0]["payload"]) == {"payload_hash"}
    assert "private" not in stream.getvalue()


def test_decision_amount_stays_readable(stream):
    log_event("decision.recorded", amount=Decimal("9900.00"))
    assert records(stream)[0]["amount"] == "9900.00"


def test_reserved_fields_cannot_be_overridden(stream):
    log_event("real.event", ts="forged", level_name="x", run_id="forged")
    record = records(stream)[0]
    assert record["ts"] != "forged"
    assert record["run_id"] is None


def test_formatting_failure_never_prints_the_raw_value(stream, capsys):
    configure_logging(stream)  # removes pytest's capture handler, which re-raises formatting errors
    logger.warning("bad account %d", "12345678")
    assert records(stream)[0]["event"] == "log_record_dropped"
    assert "12345678" not in stream.getvalue()
    assert "12345678" not in capsys.readouterr().err


def test_unusual_payloads_do_not_crash_the_run(stream):
    cycle = {}
    cycle["self"] = cycle

    class Broken:
        def __str__(self):
            raise RuntimeError("boom")

    for payload in ({1: "a", "b": 2}, cycle, Broken()):
        log_event("tool.result", payload=payload)
    assert [record["payload"] for record in records(stream)] == [{"summary_unavailable": True}] * 3
