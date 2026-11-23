"""Regressions for the controls defects found by the independent review."""

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import finagent.tools.block_submit as block_submit
from finagent.config.clock import FixedClock
from finagent.controls.decision import evaluate
from finagent.llm.fake import FakeLLM
from finagent.orchestration import nodes
from finagent.orchestration.checkpoint import resume
from finagent.orchestration.runner import Runner, build_deps
from finagent.persistence.db import connect
from finagent.service.run_service import DecisionCommand, RunService, RunServiceError, StartCommand
from finagent.tools.block_submit import _check_write_deadline, write_deadline
from fixtures.builder import build_case
from tests.acceptance.support_graph import apply_decision, counts, run_case
from tests.acceptance.support_resilience import SimulatedCrash, make_workspace, restart, rows, start

ROOT = Path(__file__).resolve().parents[2]
APPROVE = {"action": "approve", "approver": "cc-manager-01"}


def outcome(overrides):
    case = build_case("FIN-001", overrides)
    return evaluate(case, FixedClock(case.as_of))


def failing(result):
    return {item.rule_id for item in result.findings if item.outcome not in {"PASS", "UNKNOWN"}}


def line(ref, line_id, qty, price, code="GST"):
    total = f"{qty * float(price):.2f}"
    return {
        "line_id": line_id,
        "po_line_ref": ref,
        "description_id": "DESC-X",
        "qty": qty,
        "unit_price": price,
        "tax_code": code,
        "line_total": total,
    }


def test_line_total_must_equal_quantity_times_price():
    result = outcome({"invoice.lines[0].line_total": "6500.00"})
    assert result.outcome == "HOLD_FOR_INFORMATION"
    assert "REQ-MATCHES-INVOICE" in failing(result)


def test_price_variance_cannot_be_split_across_lines():
    result = outcome(
        {
            "invoice.lines[0].qty": 20,
            "invoice.lines[0].unit_price": "152.40",
            "invoice.lines[0].line_total": "3048.00",
            "invoice.lines[2]": line("PO-L1", "L3", 20, "152.40"),
        }
    )
    assert "TOL-GOODS" in failing(result)


def test_freight_is_measured_on_the_billed_value():
    result = outcome(
        {
            "po.freight_permitted": True,
            "po.lines[2]": {
                "po_line_ref": "PO-L3",
                "kind": "freight",
                "qty": 1,
                "unit_price": "50.00",
                "line_value": "50.00",
            },
            "invoice.lines[2]": line("PO-L3", "L3", 100, "50.00", "FRE"),
        }
    )
    assert "TOL-FREIGHT" in failing(result)


def test_quantity_is_capped_at_the_ordered_quantity():
    result = outcome(
        {
            "po.receipts[1].qty": 150,
            "evidence.goods_receipt_note.lines[1].qty": 150,
            "invoice.lines[1].qty": 150,
            "invoice.lines[1].line_total": "4500.00",
        }
    )
    assert "QTY-RECEIVED" in failing(result)


def test_a_receipt_dated_after_the_case_date_holds():
    result = outcome({"po.receipts[0].date": "2026-12-25"})
    assert result.outcome == "HOLD_FOR_INFORMATION"
    assert "TYPED-RECORD-PRECEDENCE" in failing(result)


def test_approver_limit_covers_the_po_commitment():
    paused = run_case("FIN-001", overrides={"po.totals.incl_tax": "40000.00"})
    response = paused.deps.approvals.decide(paused.pending_approval, "cc-manager-01", "approve")
    assert response.code == "APPROVER_NOT_ELIGIBLE"
    assert counts(paused)["ledger"] == {}


def _edit(home, name, change):
    path = home / "cases" / "FIN-001" / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "change"),
    [
        ("vendor", lambda d: d.update(risk_flags=["fraud_flag"])),
        ("vendor", lambda d: d.update(bank_changed_at="2026-09-15T08:00:00+10:00")),
        ("po", lambda d: d.update(approval_status="CANCELLED")),
        ("invoice", lambda d: d["remit_to"].update(fingerprint="f" * 64)),
    ],
)
def test_material_change_after_approval_invalidates(name, change):
    home = make_workspace()
    paused = start(home=home)
    paused.deps.approvals.decide(paused.pending_approval, "cc-manager-01", "approve")
    _edit(home, name, change)
    view = paused.runner.resume(paused.run_id)
    assert view.status == "AWAITING_APPROVAL"
    assert paused.deps.approvals.get(paused.pending_approval).status == "INVALIDATED"
    assert rows(paused.deps.sandbox_db)["ledger"] == 0


def test_limit_cut_after_approval_invalidates():
    home = make_workspace()
    paused = start(home=home)
    paused.deps.approvals.decide(paused.pending_approval, "cc-manager-01", "approve")

    def cut(data):
        records = data["records"] if isinstance(data, dict) else data
        for record in records:
            if record["approver_id"] == "cc-manager-01":
                record["limit"] = "1000.00"

    _edit(home, "authority", cut)
    paused.runner.resume(paused.run_id)
    assert rows(paused.deps.sandbox_db)["ledger"] == 0


def test_crash_at_submit_revalidates_before_retrying(monkeypatch):
    home = make_workspace()
    paused = start(home=home)
    paused.deps.approvals.decide(paused.pending_approval, "cc-manager-01", "approve")
    real = nodes.HANDLERS["SUBMIT"]
    monkeypatch.setitem(nodes.HANDLERS, "SUBMIT", lambda *_: (_ for _ in ()).throw(SimulatedCrash("x")))
    with pytest.raises(SimulatedCrash):
        paused.runner.resume(paused.run_id)
    monkeypatch.setitem(nodes.HANDLERS, "SUBMIT", real)
    _edit(home, "vendor", lambda d: d.update(status="BLOCKED"))
    deps, _ = restart("FIN-001", home)
    view = resume(paused.run_id, deps)
    assert view.status == "AWAITING_APPROVAL"
    assert rows(deps.sandbox_db)["ledger"] == 0


def test_concurrent_approvals_post_once_and_never_reject(tmp_path):
    service = RunService(tmp_path, FakeLLM)
    case = ROOT / "fixtures/cases/FIN-001"
    run_id = service.start(StartCommand(case_dir=case)).run_id
    errors = []

    def approve():
        try:
            RunService(tmp_path, FakeLLM).approve(DecisionCommand(run_id=run_id, approver="cc-manager-01"))
        except Exception as exc:  # recorded so the assertion names it
            errors.append(repr(exc))

    threads = [threading.Thread(target=approve) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert service.get(run_id).status == "COMPLETE"
    assert rows(tmp_path / "erp_sandbox.db") == {"block": 0, "ledger": 1, "idempotency": 1}


def test_a_write_past_its_deadline_rolls_back(tmp_path):
    with write_deadline(0.0):
        time.sleep(0.01)
        with pytest.raises(TimeoutError):
            _check_write_deadline()
    _check_write_deadline()  # the deadline does not outlive its write


def test_late_submit_leaves_no_row(monkeypatch):
    paused = start()
    paused.deps.approvals.decide(paused.pending_approval, "cc-manager-01", "approve")
    monkeypatch.setattr(block_submit, "_check_write_deadline", _always_late())
    view = paused.runner.resume(paused.run_id)
    assert view.status == "FAILED"
    assert rows(paused.deps.sandbox_db) == {"block": 0, "ledger": 0, "idempotency": 0}


def _always_late():
    def check():
        raise TimeoutError

    return check


def test_a_stricter_run_replaces_an_undecided_request():
    home = make_workspace()
    first = start(home=home)
    _edit(home, "vendor", lambda d: d.update(status="BLOCKED"))
    second = start(home=home)
    assert second.pending_approval != first.pending_approval
    assert first.deps.approvals.get(first.pending_approval).status == "INVALIDATED"
    assert second.deps.approvals.get(second.pending_approval).run_id == second.run_id


def _vendor_with(**fields):
    return {f"vendor.{key}": value for key, value in fields.items()}


def test_vendor_creator_cannot_dismiss_a_protective_action():
    paused = run_case("FIN-003", overrides=_vendor_with(created_by="fc-01"))
    response = paused.deps.approvals.decide(paused.pending_approval, "fc-01", "reject", "no")
    assert response.code == "APPROVER_NOT_ELIGIBLE"


def test_recent_vendor_modifier_cannot_approve():
    change = [{"by": "cc-manager-01", "at": "2026-09-14T10:00:00+10:00"}]
    paused = run_case("FIN-001", overrides=_vendor_with(modification_history=change))
    response = paused.deps.approvals.decide(paused.pending_approval, "cc-manager-01", "approve")
    assert (response.code, response.status) == ("APPROVER_NOT_ELIGIBLE", "PENDING")


def test_old_vendor_modification_does_not_block():
    change = [{"by": "cc-manager-01", "at": "2026-08-01T10:00:00+10:00"}]
    paused = run_case("FIN-001", overrides=_vendor_with(modification_history=change))
    response = paused.deps.approvals.decide(paused.pending_approval, "cc-manager-01", "approve")
    assert response.code == "APPROVED"


def test_service_confirmer_counts_as_a_receipter():
    overrides = {"po.receipts[0].service_confirmed_by": "svc-owner-01"}
    paused = run_case("FIN-001", overrides=overrides)
    assert "svc-owner-01" in paused.deps.approvals.get(paused.pending_approval).receipters


def test_approval_evidence_cannot_be_rewritten():
    report = run_case("FIN-001", decision=APPROVE)
    conn = connect(report.deps.app_db)
    try:
        approval_id = conn.execute("SELECT approval_id FROM approvals").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE approvals SET approver_id = 'x' WHERE approval_id = ?", (approval_id,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM approvals WHERE approval_id = ?", (approval_id,))
    finally:
        conn.close()


def test_stored_limit_is_the_limit_applied():
    overrides = {"authority[0].limit": "20000.00"}
    report, _ = apply_decision(run_case("FIN-001", overrides=overrides), APPROVE)
    conn = connect(report.deps.app_db)
    try:
        stored = conn.execute("SELECT limit_amount FROM approvals").fetchone()[0]
    finally:
        conn.close()
    assert stored == "10000.00"


def test_decision_audit_names_who_decided_and_under_what_authority():
    report = run_case("FIN-001", decision=APPROVE)
    event = next(item for item in report.audit if item["event"] == "approval_decided")
    assert {"approver_id", "role", "limit", "register_version", "case_id", "decision"} <= set(event)


def test_restricted_policy_access_is_audited():
    report = run_case("FIN-001")
    assert any(item["event"] == "restricted_access" for item in report.audit)


def test_a_refused_command_does_not_drive_the_run(tmp_path):
    service = RunService(tmp_path, FakeLLM)
    run_id = service.start(StartCommand(case_dir=ROOT / "fixtures/cases/FIN-001")).run_id
    deps = build_deps(
        ROOT / "fixtures/cases/FIN-001",
        tmp_path,
        FixedClock(service.get(run_id).result and _as_of()),
        FakeLLM(),
    )
    approval = Runner(deps).view(run_id).pending_approval_id
    deps.approvals.decide(approval, "cc-manager-01", "approve")
    deps.conn.close()
    response = service.reject(DecisionCommand(run_id=run_id, approver="mallory", reason="no"))
    assert (response.applied, response.status) == (False, "AWAITING_APPROVAL")
    assert rows(tmp_path / "erp_sandbox.db")["ledger"] == 0


def _as_of():
    from tests.acceptance.support_graph import AS_OF

    return AS_OF


def test_as_of_cannot_backdate_a_case(tmp_path):
    from datetime import datetime

    command = StartCommand(
        case_dir=ROOT / "fixtures/cases/FIN-001", as_of=datetime.fromisoformat("2026-09-09T09:00:00+10:00")
    )
    with pytest.raises(RunServiceError):
        RunService(tmp_path, FakeLLM).start(command)


def test_checkpoints_hold_no_supplier_document_text(tmp_path):
    case = tmp_path / "cases" / "FIN-003"
    shutil.copytree(ROOT / "fixtures/cases/FIN-003", case)
    attachment = case / "supplier_attachment.md"
    attachment.write_text(attachment.read_text(encoding="utf-8") + "\nContact ap@evil.example\n", "utf-8")
    home = tmp_path / "home"
    RunService(home, FakeLLM).start(StartCommand(case_dir=case))
    conn = connect(home / "finagent.db")
    try:
        stored = " ".join(row[0] for row in conn.execute("SELECT state_json FROM checkpoints"))
    finally:
        conn.close()
    assert "ap@evil.example" not in stored


def test_re_evaluation_reads_a_fresh_purchase_order():
    home = make_workspace()
    paused = start(home=home)
    paused.deps.approvals.decide(paused.pending_approval, "cc-manager-01", "approve")
    _edit(home, "po", lambda d: d.update(approval_status="CANCELLED"))
    view = paused.runner.resume(paused.run_id)
    assert any(item.rule_id == "PO-APPROVED" and item.outcome != "PASS" for item in view.result.findings)


def test_fraud_escalation_survives_a_resume_that_re_evaluates():
    report = run_case("FIN-003", decision={"action": "approve", "approver": "fc-01"})
    assert report.outcome == "ESCALATE_CONTROL_REVIEW"
    assert counts(report)["ledger"] == {"ESCALATE": 1}


def test_one_transient_read_during_revalidation_is_retried():
    home = make_workspace()
    paused = start(home=home)
    paused.deps.approvals.decide(paused.pending_approval, "cc-manager-01", "approve")
    real, state = paused.deps.tools["get_vendor_record"], {"failed": False}

    def flaky(request):
        if not state["failed"]:
            state["failed"] = True
            raise ConnectionError
        return real(request)

    paused.deps.tools["get_vendor_record"] = flaky
    assert paused.runner.resume(paused.run_id).status == "COMPLETE"


def test_notes_and_rejection_reasons_are_stored_redacted(tmp_path):
    case = tmp_path / "cases" / "FIN-001"
    shutil.copytree(ROOT / "fixtures/cases/FIN-001", case)
    request = json.loads((case / "request.json").read_text(encoding="utf-8"))
    request["notes"] = "account 123456789, contact ap@example.test"
    (case / "request.json").write_text(json.dumps(request), encoding="utf-8")
    home = tmp_path / "home"
    service = RunService(home, FakeLLM)
    run_id = service.start(StartCommand(case_dir=case)).run_id
    service.reject(DecisionCommand(run_id=run_id, approver="cc-manager-01", reason="acct 987654321"))
    conn = connect(home / "finagent.db")
    try:
        stored = " ".join(row[0] for row in conn.execute("SELECT state_json FROM checkpoints"))
        reason = conn.execute("SELECT reason FROM approvals").fetchone()[0]
    finally:
        conn.close()
    assert "123456789" not in stored and "ap@example.test" not in stored
    assert "987654321" not in reason


def test_retrieval_runs_through_the_tool_contract_and_is_audited():
    report = run_case("FIN-001")
    assert report.tool_attempts["retrieve_finance_documents"] >= 1
    events = [item for item in report.audit if item["event"] == "retrieval_query"]
    assert events and all(item["run_id"] == report.run_id and item["duration_ms"] > 0 for item in events)


def test_failed_retrieval_fails_the_run_safely(monkeypatch):
    from finagent.rag.retrieve import Retriever

    def down(*_args, **_kwargs):
        raise ConnectionError

    monkeypatch.setattr(Retriever, "retrieve_finance_documents", down)
    report = run_case("FIN-001")
    assert report.status == "FAILED"
    assert counts(report) == {"block": 0, "ledger": {}}


def test_audit_records_real_time_beside_business_time():
    report = run_case("FIN-001")
    assert all(item["recorded_at"] > item["ts"] for item in report.audit)


def _pending(report):
    return report.runner.view(report.run_id).pending_approval_id


def test_two_re_evaluations_stay_within_the_tool_budget():
    home = make_workspace("FIN-002")
    paused = start("FIN-002", home=home)
    path = home / "cases" / "FIN-002" / "history.json"
    for number in (1, 2):
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data["records"] if isinstance(data, dict) else data
        records.append({**records[0], "record_id": f"H-900{number}"})
        path.write_text(json.dumps(data), encoding="utf-8")
        paused.deps.approvals.decide(_pending(paused), "cc-manager-01", "approve")
        view = paused.runner.resume(paused.run_id)
        assert view.status == "AWAITING_APPROVAL"
