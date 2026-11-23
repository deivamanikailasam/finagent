"""WP 4.3: mock tools, fault injection and ledger visibility."""

import json
import shutil
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from finagent.config.clock import SYDNEY, FixedClock
from finagent.rag.loader import ingest
from finagent.rag.retrieve import Retriever, load_case_documents
from finagent.tools.block_submit import ApprovalView, derive_key
from finagent.tools.declarations import load_tool_declarations
from finagent.tools.factory import FaultGate, build_tools
from finagent.tools.runner import RunContext, ToolHTTPError, ToolRunner
from finagent.tools.schemas import DecisionPayload, payload_hash_of

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "fixtures/cases"
CONFIG = load_tool_declarations(ROOT / "config/tools.yaml")
AS_OF = datetime(2026, 9, 15, 9, 0, tzinfo=SYDNEY)
KEY = "0" * 64
RETRIEVER = Retriever(ingest(ROOT / "corpus", ROOT / "config/registry.yaml"), load_case_documents(CASES))
CASE_IDS = ["FIN-001", "FIN-002", "FIN-003", "FIN-004", "FIN-005"]


class Approvals:
    def __init__(self):
        self.records = {}

    def get(self, approval_id):
        return self.records.get(approval_id)


class InlineExecutor:
    def __init__(self):
        self.sleeps = []

    def run(self, fn, timeout_s, on_late):
        return fn()

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def setup(case_id, tmp_path, case_dir=None, sandbox=None):
    approvals = Approvals()
    sandbox = sandbox or tmp_path / "erp_sandbox.db"
    tools = build_tools(
        case_dir or CASES / case_id, sandbox, CONFIG, RETRIEVER, approvals, FixedClock(AS_OF), KEY
    )
    events = []
    context = RunContext("run-1", case_id, events.append, FixedClock(AS_OF))
    return ToolRunner(tools, CONFIG, InlineExecutor(), context, max_tool_calls=50), approvals, events


def case_json(case_id, name):
    return json.loads((CASES / case_id / f"{name}.json").read_text(encoding="utf-8"))


def read_args(case_id):
    request = case_json(case_id, "request")
    return {
        "get_invoice_document": {"invoice_ref": request["invoice_ref"]},
        "get_vendor_record": {"vendor_id": request["vendor_id"]},
        "get_purchase_order": {"po_ref": case_json(case_id, "invoice")["po_ref"]},
        "get_authority_record": {},
        "check_invoice_history": {"vendor_id": request["vendor_id"]},
        "retrieve_finance_documents": {"query": "approval limit", "principal_role": "agent", "as_of": AS_OF},
    }


def post(runner, approvals, run_id="run-1", case_id="FIN-001", number="inv1001"):
    body = DecisionPayload(
        case_id=case_id,
        vendor_id="V-100",
        invoice_no_norm=number,
        amount="9900.00",
        currency="AUD",
        invoice_date="2026-09-01",
        po_ref="PO-4471",
        outcome="APPROVE_FOR_POSTING",
        line_quantities=[{"po_line_ref": "PO-L1", "qty": 40}, {"po_line_ref": "PO-L2", "qty": 100}],
    )
    payload_hash = payload_hash_of(body)
    approvals.records["a-1"] = ApprovalView("a-1", run_id, payload_hash, "POST", "APPROVED")
    return runner.call(
        "submit_finance_decision",
        {
            "run_id": run_id,
            "action": "POST",
            "authorisation": {"type": "HUMAN_APPROVAL", "id": "a-1"},
            "payload": body.model_dump(mode="json"),
            "payload_hash": payload_hash,
            "idempotency_key": derive_key(run_id, "a-1", "POST"),
        },
    )


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_read_tool_returns_a_schema_valid_output(case_id, tmp_path):
    runner, _, _ = setup(case_id, tmp_path)
    for name, arguments in read_args(case_id).items():
        result = runner.call(name, arguments)
        if case_id == "FIN-004" and name == "get_purchase_order":
            assert result.status == "unknown"
        else:
            assert result.status == "ok", name


def test_fin_004_po_times_out_on_every_attempt(tmp_path):
    runner, _, events = setup("FIN-004", tmp_path)
    result = runner.call("get_purchase_order", read_args("FIN-004")["get_purchase_order"])
    assert (result.status, result.attempts, result.unknown.reason) == ("unknown", 3, "timeout")
    assert [event["attempt"] for event in events] == [1, 2, 3]


def test_fail_once_503_recovers_on_the_second_attempt(tmp_path):
    case_dir = tmp_path / "FIN-001"
    shutil.copytree(CASES / "FIN-001", case_dir)
    meta = json.loads((case_dir / "meta.json").read_text())
    meta["faults"] = {"get_purchase_order": "fail_once_503"}
    (case_dir / "meta.json").write_text(json.dumps(meta))
    runner, _, _ = setup("FIN-001", tmp_path, case_dir)
    result = runner.call("get_purchase_order", read_args("FIN-001")["get_purchase_order"])
    assert (result.status, result.attempts) == ("ok", 2)


def test_fault_gate_modes():
    FaultGate("ok").check()
    with pytest.raises(TimeoutError):
        FaultGate("timeout_always").check()
    gate = FaultGate("fail_once_503")
    with pytest.raises(ToolHTTPError):
        gate.check()
    gate.check()
    with pytest.raises(ValueError):
        FaultGate("sometimes")


def test_unknown_fault_tool_is_rejected(tmp_path):
    case_dir = tmp_path / "FIN-001"
    shutil.copytree(CASES / "FIN-001", case_dir)
    meta = json.loads((case_dir / "meta.json").read_text())
    meta["faults"] = {"release_payment": "ok"}
    (case_dir / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="unknown tool"):
        setup("FIN-001", tmp_path, case_dir)


def test_tool_with_an_undeclared_impl_is_rejected(tmp_path):
    tools = [
        tool.model_copy(update={"impl": "real"}) if tool.name == "get_vendor_record" else tool
        for tool in CONFIG.tools
    ]
    config = CONFIG.model_copy(update={"tools": tuple(tools)})
    with pytest.raises(ValueError, match="no real implementation"):
        build_tools(
            CASES / "FIN-001", tmp_path / "x.db", config, RETRIEVER, Approvals(), FixedClock(AS_OF), KEY
        )


def test_history_includes_a_posted_ledger_row(tmp_path):
    runner, approvals, _ = setup("FIN-001", tmp_path)
    assert post(runner, approvals).status == "ok"
    records = runner.call("check_invoice_history", {"vendor_id": "V-100"}).value.records
    assert len(records) == 1
    record = records[0]
    assert (record.record_id, record.status, record.invoice_no_norm) == ("L-1", "POSTED", "inv1001")
    assert (record.gross, record.po_number, record.originating_run) == (
        Decimal("9900.00"),
        "PO-4471",
        "run-1",
    )
    assert record.prior_outcome == "APPROVE_FOR_POSTING"
    assert len(record.fingerprint) == 64


def test_history_combines_fixture_and_ledger_rows(tmp_path):
    sandbox = tmp_path / "erp_sandbox.db"
    runner, approvals, _ = setup("FIN-001", tmp_path, sandbox=sandbox)
    post(runner, approvals, number="inv7777")
    second, _, _ = setup("FIN-002", tmp_path, sandbox=sandbox)
    records = second.call("check_invoice_history", {"vendor_id": "V-100"}).value.records
    assert [record.record_id for record in records] == ["H-0001", "L-1"]


def test_posted_quantities_count_as_invoiced_on_the_po(tmp_path):
    runner, approvals, _ = setup("FIN-001", tmp_path)
    arguments = read_args("FIN-001")["get_purchase_order"]
    assert [
        line.qty_invoiced_to_date for line in runner.call("get_purchase_order", arguments).value.lines
    ] == [
        0,
        0,
    ]
    post(runner, approvals)
    assert [
        line.qty_invoiced_to_date for line in runner.call("get_purchase_order", arguments).value.lines
    ] == [
        40,
        100,
    ]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_invoice_document", {"invoice_ref": "INV-9999"}),
        ("get_vendor_record", {"vendor_id": "V-999"}),
        ("get_purchase_order", {"po_ref": "PO-9999"}),
    ],
)
def test_a_record_outside_the_case_is_unknown(name, arguments, tmp_path):
    runner, _, _ = setup("FIN-001", tmp_path)
    result = runner.call(name, arguments)
    assert result.status == "unknown"
    assert result.attempts == 1


def test_mocks_do_not_import_network_clients():
    source = (ROOT / "src/finagent/tools/mocks/case_tools.py").read_text(encoding="utf-8")
    for module in ("socket", "http", "urllib", "requests", "httpx", "anthropic"):
        assert f"import {module}" not in source
