# ruff: noqa: I001
"""Acceptance tests for src/finagent/tools/runner.py (WP 4.A). Values come from decision log VII."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from finagent.config.clock import FixedClock
from finagent.tools.declarations import load_tool_declarations
from finagent.tools.runner import BudgetExceeded, RunContext, ToolHTTPError, ToolRunner
from finagent.tools.schemas import (
    BlockPayload,
    CheckInvoiceHistoryOutput,
    DecisionPayload,
    GetAuthorityRecordOutput,
    RetrieveFinanceDocumentsOutput,
    WriteOutput,
    payload_hash_of,
)
from fixtures.builder import build_case

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_tool_declarations(ROOT / "config/tools.yaml")
READ_TOOLS = [tool.name for tool in CONFIG.tools if tool.access == "read"]
WRITE_TOOLS = [tool.name for tool in CONFIG.tools if tool.access == "write"]
AS_OF = datetime(2026, 9, 15, tzinfo=UTC)
CASE = build_case("FIN-001")
BLOCK_BODY = BlockPayload(case_id="FIN-001", vendor_id="V-100", invoice_no_norm="inv1001")
DECISION_BODY = DecisionPayload(
    case_id="FIN-001",
    vendor_id="V-100",
    invoice_no_norm="inv1001",
    amount="9900.00",
    currency="AUD",
    invoice_date="2026-09-01",
    po_ref="PO-4471",
    outcome="APPROVE_FOR_POSTING",
)
KEY = hashlib.sha256(b"run-1|auth-1|action").hexdigest()

READ_ARGS = {
    "get_invoice_document": {"invoice_ref": "INV-1001"},
    "get_vendor_record": {"vendor_id": "V-100"},
    "get_purchase_order": {"po_ref": "PO-4471"},
    "get_authority_record": {},
    "check_invoice_history": {"vendor_id": "V-100", "invoice_no_norm": "inv1001"},
    "retrieve_finance_documents": {"query": "approval limit", "principal_role": "agent", "as_of": AS_OF},
}
READ_VALUES = {
    "get_invoice_document": CASE.invoice,
    "get_vendor_record": CASE.vendor,
    "get_purchase_order": CASE.po,
    "get_authority_record": GetAuthorityRecordOutput(records=CASE.authority),
    "check_invoice_history": CheckInvoiceHistoryOutput(records=()),
    "retrieve_finance_documents": RetrieveFinanceDocumentsOutput(
        lanes={"authoritative": (), "case_evidence": (), "non_authoritative": ()},
        abstained={"authoritative": True, "case_evidence": True, "non_authoritative": True},
        abstain_reason={
            "authoritative": "non_positive",
            "case_evidence": "no_candidate",
            "non_authoritative": "non_positive",
        },
    ),
}
WRITE_ARGS = {
    "place_payment_block": {
        "run_id": "run-1",
        "action": "BLOCK",
        "rule_id": "TOL-GOODS",
        "authorisation": {"type": "SYSTEM_FAILSAFE", "id": "auth-1"},
        "payload": BLOCK_BODY.model_dump(mode="json"),
        "payload_hash": payload_hash_of(BLOCK_BODY),
        "idempotency_key": KEY,
    },
    "submit_finance_decision": {
        "run_id": "run-1",
        "action": "POST",
        "authorisation": {"type": "HUMAN_APPROVAL", "id": "auth-1"},
        "payload": DECISION_BODY.model_dump(mode="json"),
        "payload_hash": payload_hash_of(DECISION_BODY),
        "idempotency_key": KEY,
    },
}


class FakeTool:
    """Plays a script: "timeout", "503", "429", "404", "connection", "valueerror" or a value."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0
        self.received = []

    def __call__(self, validated_input):
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        self.received.append(validated_input)
        if step == "timeout":
            raise TimeoutError
        if step == "connection":
            raise ConnectionError
        if step == "valueerror":
            raise ValueError("bad value")
        if step in ("503", "429", "404"):
            raise ToolHTTPError(int(step))
        return step


class FakeExecutor:
    """No real waiting. mark_late makes the next call finish after its deadline."""

    def __init__(self):
        self.sleeps = []
        self.deadlines = []
        self._late = False

    def mark_late(self):
        self._late = True

    def run(self, fn, timeout_s, on_late):
        self.deadlines.append(timeout_s)
        if self._late:
            self._late = False
            on_late(fn())
            raise TimeoutError
        return fn()

    def sleep(self, seconds):
        self.sleeps.append(seconds)


class Harness:
    def __init__(self, name, tool, max_tool_calls=None):
        self.events = []
        self.executor = FakeExecutor()
        context = RunContext(
            run_id="run-1",
            case_id="FIN-001",
            audit=self.events.append,
            clock=FixedClock(AS_OF),
        )
        self.runner = ToolRunner({name: tool}, CONFIG, self.executor, context, max_tool_calls=max_tool_calls)

    def attempts(self):
        return [event for event in self.events if "attempt" in event]


def args_for(name):
    return dict(WRITE_ARGS[name] if name in WRITE_ARGS else READ_ARGS[name])


def test_tool_lists_match_the_decision_log():
    assert set(WRITE_TOOLS) == {"place_payment_block", "submit_finance_decision"}
    assert len(READ_TOOLS) == 6


@pytest.mark.parametrize("name", READ_TOOLS)
def test_read_tool_success_is_one_call_with_the_declared_deadline(name):
    tool = FakeTool(READ_VALUES[name])
    harness = Harness(name, tool)
    result = harness.runner.call(name, args_for(name))
    assert (result.status, result.value, result.attempts) == ("ok", READ_VALUES[name], 1)
    assert tool.calls == 1
    assert harness.executor.deadlines == [2.0]


@pytest.mark.parametrize("name", READ_TOOLS)
def test_timeout_on_every_attempt_is_three_audited_calls_then_unknown(name):
    tool = FakeTool("timeout")
    harness = Harness(name, tool)
    result = harness.runner.call(name, args_for(name))
    assert tool.calls == 3
    assert result.status == "unknown"
    assert (result.unknown.source, result.unknown.reason) == (name, "timeout")
    assert [event["attempt"] for event in harness.attempts()] == [1, 2, 3]
    assert harness.executor.sleeps == [0.2, 0.4]


@pytest.mark.parametrize("name", READ_TOOLS)
def test_503_once_then_success_matches_a_no_fault_run(name):
    faulted = FakeTool("503", READ_VALUES[name])
    clean = FakeTool(READ_VALUES[name])
    faulted_result = Harness(name, faulted).runner.call(name, args_for(name))
    clean_result = Harness(name, clean).runner.call(name, args_for(name))
    assert faulted.calls == 2
    assert faulted_result.attempts == 2
    assert faulted_result.value == clean_result.value


@pytest.mark.parametrize("step", ["429", "connection"])
def test_other_retryable_faults_are_retried(step):
    name = "get_vendor_record"
    tool = FakeTool(step, READ_VALUES[name])
    assert Harness(name, tool).runner.call(name, args_for(name)).status == "ok"
    assert tool.calls == 2


@pytest.mark.parametrize("name", READ_TOOLS)
@pytest.mark.parametrize("step", ["valueerror", "404"])
def test_non_retryable_failure_is_one_call_then_unknown(name, step):
    tool = FakeTool(step)
    result = Harness(name, tool).runner.call(name, args_for(name))
    assert tool.calls == 1
    assert result.status == "unknown"
    assert result.unknown.source == name
    assert result.unknown.reason


@pytest.mark.parametrize("name", WRITE_TOOLS)
@pytest.mark.parametrize("step", ["timeout", "503", "connection"])
def test_write_tool_is_never_retried_and_reports_submit_unknown(name, step):
    tool = FakeTool(step, WriteOutput(status="COMMITTED"))
    harness = Harness(name, tool)
    result = harness.runner.call(name, args_for(name))
    assert tool.calls == 1
    assert result.status == "SUBMIT_UNKNOWN"
    assert harness.executor.deadlines == [3.0]
    assert harness.executor.sleeps == []


@pytest.mark.parametrize("name", WRITE_TOOLS)
def test_write_tool_success(name):
    output = WriteOutput(status="COMMITTED")
    tool = FakeTool(output)
    result = Harness(name, tool).runner.call(name, args_for(name))
    assert (result.status, result.value, tool.calls) == ("ok", output, 1)


def test_late_result_is_discarded_and_audited():
    name = "get_vendor_record"
    late_vendor = CASE.vendor.model_copy(update={"status": "BLOCKED"})
    tool = FakeTool(late_vendor, CASE.vendor)
    harness = Harness(name, tool)
    harness.executor.mark_late()
    result = harness.runner.call(name, args_for(name))
    assert result.value == CASE.vendor
    assert result.value != late_vendor
    assert any(event.get("outcome") == "late_result_discarded" for event in harness.events)


def test_late_write_result_is_still_submit_unknown():
    name = "submit_finance_decision"
    tool = FakeTool(WriteOutput(status="COMMITTED"))
    harness = Harness(name, tool)
    harness.executor.mark_late()
    result = harness.runner.call(name, args_for(name))
    assert result.status == "SUBMIT_UNKNOWN"
    assert result.value is None
    assert tool.calls == 1


def test_caller_supplied_case_id_is_ignored():
    name = "get_invoice_document"
    tool = FakeTool(CASE.invoice)
    Harness(name, tool).runner.call(name, {**args_for(name), "case_id": "FIN-999"})
    assert tool.received[0].case_id == "FIN-001"


def test_retrieval_case_filter_comes_from_the_run_context():
    name = "retrieve_finance_documents"
    tool = FakeTool(READ_VALUES[name])
    arguments = {**args_for(name), "filters": {"case_id": "FIN-003"}}
    Harness(name, tool).runner.call(name, arguments)
    assert tool.received[0].filters.case_id == "FIN-001"


def test_tool_call_budget_is_enforced():
    name = "get_vendor_record"
    tool = FakeTool(CASE.vendor)
    harness = Harness(name, tool, max_tool_calls=1)
    harness.runner.call(name, args_for(name))
    with pytest.raises(BudgetExceeded):
        harness.runner.call(name, args_for(name))
    assert tool.calls == 1


def test_retry_attempts_do_not_use_extra_budget():
    name = "get_vendor_record"
    tool = FakeTool("503", "503", CASE.vendor)
    harness = Harness(name, tool, max_tool_calls=1)
    assert harness.runner.call(name, args_for(name)).status == "ok"
    assert tool.calls == 3


def test_invalid_arguments_never_reach_the_tool():
    name = "submit_finance_decision"
    tool = FakeTool(WriteOutput(status="COMMITTED"))
    result = Harness(name, tool).runner.call(name, {**args_for(name), "payload_hash": "b" * 64})
    assert tool.calls == 0
    assert result.status == "unknown"
    assert result.unknown.reason == "invalid_arguments"


def test_invalid_output_is_unknown_and_not_retried():
    name = "get_vendor_record"
    tool = FakeTool({"vendor_id": "V-100"})
    result = Harness(name, tool).runner.call(name, args_for(name))
    assert tool.calls == 1
    assert result.status == "unknown"
    assert result.unknown.reason == "invalid_output"


def test_unknown_tool_is_refused():
    harness = Harness("get_vendor_record", FakeTool(CASE.vendor))
    with pytest.raises(KeyError):
        harness.runner.call("release_payment", {})


def test_every_attempt_has_the_audit_fields():
    name = "get_vendor_record"
    harness = Harness(name, FakeTool("503", CASE.vendor))
    harness.runner.call(name, args_for(name))
    attempts = harness.attempts()
    assert len(attempts) == 2
    for event in attempts:
        assert {"ts", "run_id", "tool", "attempt", "outcome", "duration_ms"} <= set(event)
        assert (event["run_id"], event["tool"]) == ("run-1", name)
    assert "legal_name" not in str(harness.events)
