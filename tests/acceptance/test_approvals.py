# ruff: noqa: I001
"""Acceptance tests for src/finagent/approvals/service.py (decision log V)."""

from decimal import Decimal

import pytest

from tests.acceptance.support_approvals import AS_OF, approver, harness, history_record, recommendation
from tests.acceptance.support_approvals import app_db  # noqa: F401

CODE_INELIGIBLE = "APPROVER_NOT_ELIGIBLE"
EXPIRED = {
    "delegate": "delegate-01",
    "delegator": "dd-01",
    "scope": "operating expenditure",
    "start": "2026-09-01",
    "end": "2026-09-14",
}
INELIGIBLE = {
    "limit_too_low": ("cc-manager-01", "12000.00", ()),
    "delegation_expired": ("delegate-01", "9900.00", (approver("delegate-01", delegations=(EXPIRED,)),)),
    "vendor_creator": ("vg-officer-01", "9900.00", (approver("vg-officer-01"),)),
    "approver_is_requester": ("req-01", "9900.00", (approver("req-01"),)),
    "requester_above_25k": (
        "req-01",
        "25000.01",
        (approver("req-01", "Department Director", "50000.00"),),
    ),
    "receipter_above_25k": (
        "rcv-01",
        "25000.01",
        (approver("rcv-01", "Department Director", "50000.00"),),
    ),
}


def request_for(h, action="POST", **fields):
    return h.service.create_request(recommendation(action, **fields))


def test_pending_to_approved(app_db):  # noqa: F811
    h = harness(app_db)
    record = request_for(h)
    assert record.status == "PENDING"
    response = h.service.decide(record.approval_id, "cc-manager-01", "approve")
    assert (response.code, response.status) == ("APPROVED", "APPROVED")
    assert h.service.get(record.approval_id).status == "APPROVED"


def test_repeat_callback_returns_the_identical_stored_response(app_db):  # noqa: F811
    h = harness(app_db)
    record = request_for(h)
    first = h.service.decide(record.approval_id, "cc-manager-01", "approve")
    h.clock.advance(minutes=5)
    second = h.service.decide(record.approval_id, "cc-manager-01", "approve")
    assert second == first
    assert h.service.get(record.approval_id).decided_at == AS_OF


@pytest.mark.parametrize(
    ("approver_id", "decision"),
    [("cc-manager-01", "reject"), ("dd-01", "approve"), ("dd-01", "reject")],
)
def test_conflicting_decision_is_already_decided(app_db, approver_id, decision):  # noqa: F811
    h = harness(app_db)
    record = request_for(h)
    h.service.decide(record.approval_id, "cc-manager-01", "approve")
    response = h.service.decide(record.approval_id, approver_id, decision, reason="changed my mind")
    assert response.code == "ALREADY_DECIDED"
    assert h.service.get(record.approval_id).status == "APPROVED"


def test_reject_is_recorded(app_db):  # noqa: F811
    h = harness(app_db)
    record = request_for(h)
    response = h.service.decide(record.approval_id, "cc-manager-01", "reject", reason="not ours")
    assert (response.code, response.status) == ("REJECTED", "REJECTED")


def test_two_runs_for_one_invoice_share_one_request(app_db):  # noqa: F811
    h = harness(app_db)
    first = request_for(h, run_id="run-1")
    second = request_for(h, run_id="run-2")
    assert second.approval_id == first.approval_id
    assert "DUPLICATE_APPROVAL_REQUEST" in h.audit_events()
    assert app_db.execute("SELECT COUNT(*) FROM approvals WHERE status = 'PENDING'").fetchone()[0] == 1


def test_a_different_invoice_gets_its_own_request(app_db):  # noqa: F811
    h = harness(app_db)
    assert request_for(h).approval_id != request_for(h, invoice="inv2002").approval_id


def test_a_new_request_is_allowed_after_the_first_is_decided(app_db):  # noqa: F811
    h = harness(app_db)
    first = request_for(h)
    h.service.decide(first.approval_id, "cc-manager-01", "reject", reason="no")
    assert request_for(h, run_id="run-2").approval_id != first.approval_id


@pytest.mark.parametrize("action", ["POST", "REJECT"])
@pytest.mark.parametrize("name", list(INELIGIBLE))
def test_ineligible_approver_stays_pending_with_a_security_event(app_db, action, name):  # noqa: F811
    approver_id, amount, extra = INELIGIBLE[name]
    h = harness(app_db, *extra)
    record = request_for(h, action, amount=amount)
    assert h.service.check_eligibility(record.approval_id, approver_id).eligible is False
    response = h.service.decide(record.approval_id, approver_id, "approve")
    assert response.code == CODE_INELIGIBLE
    assert h.service.get(record.approval_id).status == "PENDING"
    assert "security_event" in h.audit_events()
    escalation = [item for item in h.service.escalations() if item.approval_id == record.approval_id]
    assert len(escalation) == 1
    assert escalation[0].routed_to == "Financial Control"


def test_ineligible_approver_cannot_reject_either(app_db):  # noqa: F811
    h = harness(app_db, approver("vg-officer-01"))
    record = request_for(h)
    response = h.service.decide(record.approval_id, "vg-officer-01", "reject", reason="no")
    assert response.code == CODE_INELIGIBLE
    assert h.service.get(record.approval_id).status == "PENDING"


def test_an_eligible_approver_can_decide_after_an_ineligible_attempt(app_db):  # noqa: F811
    h = harness(app_db, approver("vg-officer-01"))
    record = request_for(h)
    h.service.decide(record.approval_id, "vg-officer-01", "approve")
    assert h.service.decide(record.approval_id, "cc-manager-01", "approve").code == "APPROVED"


def test_unregistered_approver_is_not_eligible(app_db):  # noqa: F811
    h = harness(app_db)
    record = request_for(h, "HOLD")
    assert h.service.decide(record.approval_id, "nobody", "approve").code == CODE_INELIGIBLE


def test_limit_is_inclusive(app_db):  # noqa: F811
    h = harness(app_db)
    record = request_for(h, amount="10000.00")
    assert h.service.check_eligibility(record.approval_id, "cc-manager-01").eligible is True


def test_limit_is_compared_at_full_precision(app_db):  # noqa: F811
    h = harness(app_db)
    record = request_for(h, amount="10000.01")
    assert h.service.check_eligibility(record.approval_id, "cc-manager-01").eligible is False
    assert h.service.check_eligibility(record.approval_id, "dd-01").eligible is True


def test_delegation_end_date_is_inclusive_on_the_sydney_date(app_db):  # noqa: F811
    active = {**EXPIRED, "end": "2026-09-15"}
    h = harness(app_db, approver("delegate-01", delegations=(active,)))
    record = request_for(h)
    assert h.service.check_eligibility(record.approval_id, "delegate-01").eligible is True


def test_receipter_may_approve_at_exactly_25k(app_db):  # noqa: F811
    h = harness(app_db, approver("rcv-01", "Department Director", "50000.00"))
    record = request_for(h, amount="25000.00")
    assert h.service.check_eligibility(record.approval_id, "rcv-01").eligible is True


def test_financial_control_cannot_approve_a_post(app_db):  # noqa: F811
    h = harness(app_db)
    record = request_for(h)
    assert h.service.check_eligibility(record.approval_id, "fc-01").eligible is False


def test_reject_by_a_role_below_cost_centre_manager_is_ineligible(app_db):  # noqa: F811
    h = harness(app_db, approver("lead-01", "Team Lead", "50000.00"))
    record = request_for(h, "REJECT")
    assert h.service.decide(record.approval_id, "lead-01", "approve").code == CODE_INELIGIBLE
    assert h.service.get(record.approval_id).status == "PENDING"


def test_reject_by_financial_control_is_eligible(app_db):  # noqa: F811
    h = harness(app_db)
    record = request_for(h, "REJECT")
    assert h.service.check_eligibility(record.approval_id, "fc-01").eligible is True
    assert h.service.decide(record.approval_id, "fc-01", "approve").code == "APPROVED"


@pytest.mark.parametrize("action", ["HOLD", "ESCALATE"])
@pytest.mark.parametrize("approver_id", ["cc-manager-01", "dd-01", "fc-01", "lead-01"])
def test_hold_and_escalate_accept_any_registered_approver(app_db, action, approver_id):  # noqa: F811
    h = harness(app_db, approver("lead-01", "Team Lead", "100.00"))
    record = request_for(h, action, amount="49000.00")
    assert h.service.decide(record.approval_id, approver_id, "approve").code == "APPROVED"


def test_approval_record_has_the_required_evidence(app_db):  # noqa: F811
    h = harness(app_db)
    record = request_for(h)
    h.service.decide(record.approval_id, "cc-manager-01", "approve")
    stored = h.service.get(record.approval_id)
    assert stored.approver_id == "cc-manager-01"
    assert stored.role == "Cost Centre Manager"
    assert stored.limit_amount == Decimal("10000.00")
    assert stored.register_version == "v2026.08"
    assert stored.decided_at == AS_OF
    assert stored.case_id == "FIN-001"


def test_revalidate_with_no_change_stays_approved(app_db):  # noqa: F811
    h = harness(app_db)
    record = request_for(h)
    h.service.decide(record.approval_id, "cc-manager-01", "approve")
    result = h.service.revalidate(record.approval_id)
    assert (result.valid, result.status) == (True, "APPROVED")


def change_vendor_status(h):
    h.vendor.value = h.vendor.value.model_copy(update={"status": "BLOCKED"})


def change_fingerprint(h):
    h.vendor.value = h.vendor.value.model_copy(update={"fingerprint": "f" * 64})


def add_history(h):
    h.history.value = (history_record(),)


def expire_delegation(h):
    h.clock.advance(days=1)


def fail_vendor_read(h):
    h.vendor.fail = True


def fail_history_read(h):
    h.history.fail = True


def fail_authority_read(h):
    h.authority.fail = True


@pytest.mark.parametrize(
    "change",
    [
        change_vendor_status,
        change_fingerprint,
        add_history,
        fail_vendor_read,
        fail_history_read,
        fail_authority_read,
    ],
)
def test_revalidation_change_or_read_failure_invalidates(app_db, change):  # noqa: F811
    h = harness(app_db)
    record = request_for(h)
    h.service.decide(record.approval_id, "cc-manager-01", "approve")
    h.clock.advance(hours=2)
    change(h)
    result = h.service.revalidate(record.approval_id)
    stored = h.service.get(record.approval_id)
    assert (result.valid, result.status, stored.status) == (False, "INVALIDATED", "INVALIDATED")
    assert stored.revalidated_at == h.clock.now_utc()
    assert stored.revalidated_at != AS_OF


def test_revalidation_detects_an_expired_delegation(app_db):  # noqa: F811
    active = {**EXPIRED, "end": "2026-09-15"}
    h = harness(app_db, approver("delegate-01", delegations=(active,)))
    record = request_for(h)
    h.service.decide(record.approval_id, "delegate-01", "approve")
    expire_delegation(h)
    result = h.service.revalidate(record.approval_id)
    assert (result.valid, result.status) == (False, "INVALIDATED")


def test_an_invalidated_approval_cannot_be_decided_again(app_db):  # noqa: F811
    h = harness(app_db)
    record = request_for(h)
    h.service.decide(record.approval_id, "cc-manager-01", "approve")
    change_vendor_status(h)
    h.service.revalidate(record.approval_id)
    assert h.service.decide(record.approval_id, "dd-01", "approve").code == "ALREADY_DECIDED"
    assert h.service.get(record.approval_id).status == "INVALIDATED"
