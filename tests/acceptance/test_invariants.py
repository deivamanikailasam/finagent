# ruff: noqa: I001
"""End-to-end invariants of the run graph (WP 6.A, plan sections 3 and 8)."""

from pathlib import Path

import pytest
import yaml

import finagent.tools.factory as tool_factory
from finagent.llm.fake import FakeLLM
from finagent.persistence.db import connect
from finagent.tools.block_submit import derive_key, submit_finance_decision
from finagent.tools.schemas import DecisionPayload, SubmitFinanceDecisionInput, payload_hash_of
from tests.acceptance.support_approvals import approver
from tests.acceptance.support_graph import spy_submit  # noqa: F401
from tests.acceptance.support_graph import apply_decision, counts, new_home, run_case

ROOT = Path(__file__).resolve().parents[2]
EXPECTATIONS = yaml.safe_load((ROOT / "tests/scenarios/expectations.yaml").read_text(encoding="utf-8"))[
    "cases"
]
TOOLS = yaml.safe_load((ROOT / "config/tools.yaml").read_text(encoding="utf-8"))
CASE_IDS = list(EXPECTATIONS)
SECTIONS = (
    "facts",
    "calculations",
    "inferences",
    "assumptions",
    "unknowns",
    "findings",
    "exceptions",
    "actions_taken",
)
VG_AS_CCM = {"authority[3]": approver("vg-officer-01").model_dump(mode="json")}


def decision(case_id):
    block = EXPECTATIONS[case_id]["decision"]
    return {"action": block["action"], "approver": block["approver"], "repeat": block.get("repeat", False)}


def actions(result):
    return [item.action for item in result.actions_taken]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_no_submit_before_the_human_decision(case_id, spy_submit):  # noqa: F811
    report = run_case(case_id)
    assert report.status == "AWAITING_APPROVAL"
    assert report.current_node == "REQUEST_APPROVAL"
    assert spy_submit == []
    assert counts(report)["ledger"] == {}


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_outcome_is_unchanged_when_the_model_obeys_an_injection(case_id):
    good = run_case(case_id, llm=FakeLLM("good"))
    obeying = run_case(case_id, llm=FakeLLM("injection_obeying"))
    assert (obeying.outcome, obeying.primary_category) == (good.outcome, good.primary_category)
    assert counts(obeying) == counts(good)


@pytest.mark.parametrize("mode", ["malformed", "invented_citation", "stop:max_tokens", "stop:refusal"])
def test_a_bad_model_response_never_changes_the_outcome(mode):
    assert run_case("FIN-001", llm=FakeLLM(mode)).outcome == "APPROVE_FOR_POSTING"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_each_case_matches_its_signed_off_rows(case_id):
    expected = EXPECTATIONS[case_id]
    paused = run_case(case_id)
    assert (paused.outcome, paused.primary_category) == (expected["outcome"], expected["primary_category"])
    assert counts(paused) == expected["rows_before_decision"]
    finished, _ = apply_decision(paused, decision(case_id))
    assert finished.status == expected["run_status_after_decision"]
    assert counts(finished) == expected["rows_after_decision"]


def test_fin_001_pauses_with_no_rows():
    assert counts(run_case("FIN-001")) == {"block": 0, "ledger": {}}


def test_fin_004_pauses_with_a_block_a_hold_request_and_three_po_attempts():
    report = run_case("FIN-004")
    assert counts(report) == {"block": 1, "ledger": {}}
    assert report.tool_attempts["get_purchase_order"] == 3
    record = report.deps.approvals.get(report.pending_approval)
    assert record.action == "HOLD"


def test_fin_004_finishes_within_the_budget():
    report = run_case("FIN-004", decision=decision("FIN-004"))
    assert report.status == "COMPLETE"
    assert len(report.visited) <= TOOLS["budget"]["max_steps"]


def test_invalidated_path_finishes_within_the_budget():
    home = new_home()
    report = run_case("FIN-001", db=home)
    sandbox = connect(report.deps.sandbox_db)
    try:
        insert_other_invoice(sandbox)
    finally:
        sandbox.close()
    finished, _ = apply_decision(report, {"action": "approve", "approver": "cc-manager-01"})
    assert finished.status == "AWAITING_APPROVAL"
    assert finished.pending_approval != report.pending_approval
    assert report.deps.approvals.get(report.pending_approval).status == "INVALIDATED"
    assert counts(finished) == {"block": 1, "ledger": {"HOLD": 1}}
    assert len(finished.visited) <= TOOLS["budget"]["max_steps"]


class AlwaysApproved:
    def __init__(self, view):
        self.view = view

    def get(self, _approval_id):
        return self.view


def other_request(run_id, number, action="HOLD", outcome="HOLD_FOR_INFORMATION"):
    from finagent.tools.block_submit import ApprovalView

    payload = DecisionPayload(
        case_id="FIN-001",
        vendor_id="V-100",
        invoice_no_norm=number,
        amount="100.00",
        currency="AUD",
        invoice_date="2026-09-10",
        outcome=outcome,
        hold_category="TAX_QUERY" if action != "POST" else None,
    )
    payload_hash = payload_hash_of(payload)
    view = ApprovalView("other-approval", run_id, payload_hash, action, "APPROVED")
    request = SubmitFinanceDecisionInput(
        run_id=run_id,
        action=action,
        authorisation={"type": "HUMAN_APPROVAL", "id": "other-approval"},
        payload=payload,
        payload_hash=payload_hash,
        idempotency_key=derive_key(run_id, "other-approval", action),
    )
    return request, AlwaysApproved(view)


def insert_other_invoice(conn):
    """A new history row for the vendor, which makes revalidation invalidate the approval."""
    from tests.acceptance.support_graph import CLOCK

    request, approvals = other_request("other-run", "inv7777")
    submit_finance_decision(conn, request, approvals, CLOCK)


def test_no_po_reference_skips_fetch_po():
    report = run_case("FIN-001", overrides={"invoice.po_ref": None, "po": None})
    assert "FETCH_PO" not in report.visited
    assert report.primary_category == "MISSING_PO"


def test_ineligible_approver_stays_at_the_pause():
    paused = run_case("FIN-001", overrides=VG_AS_CCM)
    report, response = apply_decision(paused, {"action": "approve", "approver": "vg-officer-01"})
    assert response.code == "APPROVER_NOT_ELIGIBLE"
    assert (report.status, report.current_node) == ("AWAITING_APPROVAL", "REQUEST_APPROVAL")
    assert counts(report)["ledger"] == {}


def test_repeated_ineligible_callbacks_do_not_exhaust_the_budget():
    report = run_case("FIN-001", overrides=VG_AS_CCM)
    for _ in range(TOOLS["budget"]["max_steps"] + 1):
        report, _response = apply_decision(report, {"action": "approve", "approver": "vg-officer-01"})
    assert report.status == "AWAITING_APPROVAL"


def test_post_that_violates_the_unique_index_becomes_a_duplicate_hold(monkeypatch):
    real = tool_factory.submit_finance_decision
    state = {"raced": False}

    def racing_submit(conn, request, approvals, clock, audit=None):
        if request.action == "POST" and not state["raced"]:
            state["raced"] = True
            competing, competing_approvals = other_request(
                "race-run", "inv1001", "POST", "APPROVE_FOR_POSTING"
            )
            real(conn, competing, competing_approvals, clock)
        return real(conn, request, approvals, clock, audit=audit)

    monkeypatch.setattr(tool_factory, "submit_finance_decision", racing_submit)
    paused = run_case("FIN-001")
    report, _ = apply_decision(paused, {"action": "approve", "approver": "cc-manager-01"})
    assert report.status != "FAILED"
    assert (report.status, report.primary_category) == ("AWAITING_APPROVAL", "DUPLICATE_RISK")
    assert report.outcome == "HOLD_FOR_INFORMATION"
    assert counts(report) == {"block": 1, "ledger": {"POST": 1}}
    assert report.deps.approvals.get(paused.pending_approval).status == "INVALIDATED"
    assert report.pending_approval != paused.pending_approval
    assert report.deps.approvals.get(report.pending_approval).action == "HOLD"


def test_new_run_on_a_blocked_invoice_cannot_post():
    home = new_home()
    run_case("FIN-001", decision={"action": "reject", "approver": "cc-manager-01", "reason": "no"}, db=home)
    second = run_case("FIN-001", db=home)
    assert (second.outcome, second.primary_category) == ("HOLD_FOR_INFORMATION", "OTHER_CONTROL_RISK")
    finished, _ = apply_decision(second, {"action": "approve", "approver": "cc-manager-01"})
    assert "POST" not in counts(finished)["ledger"]


def test_max_steps_fails_the_run():
    report = run_case("FIN-001", max_steps=3)
    assert report.status == "FAILED"
    assert report.current_node == "FAILED"
    assert counts(report) == {"block": 0, "ledger": {}}


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_run_event_has_the_required_fields(case_id):
    report = run_case(case_id, decision=decision(case_id))
    assert report.audit
    for event in report.audit:
        assert {"ts", "run_id", "event", "outcome", "duration_ms"} <= set(event), event
        assert event["run_id"] == report.run_id


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_final_result_is_complete_at_the_pause_and_at_the_end(case_id):
    paused = run_case(case_id)
    finished, _ = apply_decision(paused, decision(case_id))
    for report in (paused, finished):
        for section in SECTIONS:
            assert hasattr(report.result, section), section
        assert report.result.recommendation.outcome == report.outcome
        assert report.result.facts
        assert all(fact.source_id for fact in report.result.facts)


def test_fin_001_actions_taken():
    paused = run_case("FIN-001")
    assert actions(paused.result) == ["approval_request"]
    finished, _ = apply_decision(paused, decision("FIN-001"))
    assert actions(finished.result) == ["approval_request", "POST"]


def test_fin_004_actions_taken():
    paused = run_case("FIN-004")
    assert actions(paused.result) == ["BLOCK", "approval_request"]
    finished, _ = apply_decision(paused, decision("FIN-004"))
    assert actions(finished.result) == ["BLOCK", "approval_request", "HOLD"]


def test_approver_reject_blocks_and_ends_for_manual_review():
    report = run_case("FIN-001", decision={"action": "reject", "approver": "cc-manager-01", "reason": "no"})
    assert report.status == "REJECTED_BY_APPROVER"
    assert counts(report) == {"block": 1, "ledger": {}}
