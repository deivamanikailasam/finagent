"""WP 4.3: every tool and retrieval event has ts, run_id, event, outcome and duration_ms."""

import io
import json
from datetime import datetime
from pathlib import Path

import pytest

from finagent.config.clock import SYDNEY, FixedClock
from finagent.obs.log import configure_logging, logger, run_context
from finagent.rag.loader import ingest
from finagent.rag.retrieve import RetrievalDenied, Retriever, load_case_documents
from finagent.tools.block_submit import ApprovalView, block_authorisation_id, derive_key
from finagent.tools.declarations import load_tool_declarations
from finagent.tools.factory import build_tools
from finagent.tools.runner import RunContext, ToolRunner
from finagent.tools.schemas import BlockPayload, DecisionPayload, payload_hash_of

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "fixtures/cases"
CONFIG = load_tool_declarations(ROOT / "config/tools.yaml")
AS_OF = datetime(2026, 9, 15, 9, 0, tzinfo=SYDNEY)
REQUIRED = {"ts", "run_id", "event", "outcome", "duration_ms"}
RETRIEVER = Retriever(ingest(ROOT / "corpus", ROOT / "config/registry.yaml"), load_case_documents(CASES))


class Approvals:
    def __init__(self):
        self.records = {}

    def get(self, approval_id):
        return self.records.get(approval_id)


class InlineExecutor:
    def run(self, fn, timeout_s, on_late):
        return fn()

    def sleep(self, seconds):
        pass


@pytest.fixture
def log_stream():
    handlers, level = list(logger.handlers), logger.level
    stream = io.StringIO()
    configure_logging(stream, FixedClock(AS_OF))
    yield stream
    logger.handlers[:] = handlers
    logger.setLevel(level)


def records(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_every_tool_event_has_the_required_fields(tmp_path):
    events = []
    approvals = Approvals()
    clock = FixedClock(AS_OF)
    tools = build_tools(
        CASES / "FIN-001",
        tmp_path / "s.db",
        CONFIG,
        RETRIEVER,
        approvals,
        clock,
        "0" * 64,
        audit=events.append,
    )
    runner = ToolRunner(tools, CONFIG, InlineExecutor(), RunContext("run-1", "FIN-001", events.append, clock))
    decision = DecisionPayload(
        case_id="FIN-001",
        vendor_id="V-100",
        invoice_no_norm="inv1001",
        amount="9900.00",
        currency="AUD",
        invoice_date="2026-09-01",
        outcome="HOLD_FOR_INFORMATION",
        hold_category="TAX_QUERY",
    )
    decision_hash = payload_hash_of(decision)
    approvals.records["a-1"] = ApprovalView("a-1", "run-1", decision_hash, "HOLD", "APPROVED")
    block_body = BlockPayload(case_id="FIN-001", vendor_id="V-100", invoice_no_norm="inv1001")
    authorisation_id = block_authorisation_id("run-1", "TAX-CHECK")
    calls = {
        "get_invoice_document": {"invoice_ref": "INV-1001"},
        "get_vendor_record": {"vendor_id": "V-100"},
        "get_purchase_order": {"po_ref": "PO-4471"},
        "get_authority_record": {},
        "check_invoice_history": {"vendor_id": "V-100"},
        "retrieve_finance_documents": {"query": "approval limit", "principal_role": "agent", "as_of": AS_OF},
        "place_payment_block": {
            "run_id": "run-1",
            "action": "BLOCK",
            "rule_id": "TAX-CHECK",
            "authorisation": {"type": "SYSTEM_FAILSAFE", "id": authorisation_id},
            "payload": block_body.model_dump(mode="json"),
            "payload_hash": payload_hash_of(block_body),
            "idempotency_key": derive_key("run-1", authorisation_id, "BLOCK"),
        },
        "submit_finance_decision": {
            "run_id": "run-1",
            "action": "HOLD",
            "authorisation": {"type": "HUMAN_APPROVAL", "id": "a-1"},
            "payload": decision.model_dump(mode="json"),
            "payload_hash": decision_hash,
            "idempotency_key": derive_key("run-1", "a-1", "HOLD"),
        },
    }
    assert set(calls) == {tool.name for tool in CONFIG.tools}
    for name, arguments in calls.items():
        assert runner.call(name, arguments).status == "ok", name
    assert {event["tool"] for event in events} == set(calls)
    for event in events:
        assert REQUIRED <= set(event), event
        assert event["run_id"] == "run-1"
    assert "Example Supplies" not in str(events)


def test_every_retrieval_event_has_the_required_fields(log_stream):
    with run_context("run-1"):
        RETRIEVER.retrieve_finance_documents("bank account change vendor master", None, "agent", AS_OF)
        RETRIEVER.fetch_by_id("FIN-POL-004#s2", "agent", AS_OF)
        RETRIEVER.fetch_by_id("FIN-POL-999#s1", "agent", AS_OF)
        with pytest.raises(RetrievalDenied):
            RETRIEVER.fetch_by_id("FIN-POL-004#s2", "read_only", AS_OF)
        with pytest.raises(RetrievalDenied):
            RETRIEVER.retrieve_finance_documents("bank", None, "intruder", AS_OF)
    logged = records(log_stream)
    names = {record["event"] for record in logged}
    assert {
        "retrieval.query",
        "retrieval.fetch",
        "restricted_access",
        "restricted_denied",
        "retrieval_denied",
    } <= names
    for record in logged:
        assert REQUIRED <= set(record), record
        assert record["run_id"] == "run-1"
        assert record["duration_ms"] is not None
