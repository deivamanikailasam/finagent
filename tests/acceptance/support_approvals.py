# ruff: noqa: I001
"""Approval test support (WP 6.A).

Contract for src/finagent/approvals/service.py:
  ApprovalRequest(run_id, case_id, action, payload, vendor_id, vendor_name, requester, receipters,
                  exceptions=(), citations=())
  ApprovalService(conn, clock, authority, vendor, history)   the last three are zero-argument read tools
    create_request(request) -> record
    decide(approval_id, approver_id, decision, reason=None) -> response with .code and .status
    check_eligibility(approval_id, approver_id) -> result with .eligible and .reason
    revalidate(approval_id) -> result with .valid and .status
    get(approval_id) -> record
    escalations() -> records with .approval_id, .routed_to and .reason
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from finagent.approvals.service import ApprovalRequest, ApprovalService
from finagent.config.clock import SYDNEY, local_date
from finagent.persistence.db import connect, migrate
from finagent.tools.schemas import AuthorityRecord, DecisionPayload, HistoryRecord
from fixtures.builder import build_case

AS_OF = datetime(2026, 9, 15, 9, 0, tzinfo=SYDNEY)
OUTCOMES = {
    "POST": "APPROVE_FOR_POSTING",
    "REJECT": "REJECT_DUPLICATE",
    "HOLD": "HOLD_FOR_INFORMATION",
    "ESCALATE": "ESCALATE_CONTROL_REVIEW",
}


class MutableClock:
    def __init__(self, instant=AS_OF):
        self.instant = instant

    def now_utc(self):
        return self.instant

    def as_of(self):
        return local_date(self.instant)

    def advance(self, **delta):
        self.instant += timedelta(**delta)


class RecordTool:
    """A read tool whose value can change, or fail, between calls."""

    def __init__(self, value):
        self.value = value
        self.fail = False

    def __call__(self):
        if self.fail:
            raise TimeoutError
        return self.value


@pytest.fixture
def app_db(tmp_path):
    conn = connect(tmp_path / "finagent.db")
    migrate(conn, "app")
    yield conn
    conn.close()


def approver(approver_id, role="Cost Centre Manager", limit="10000.00", delegations=()):
    return AuthorityRecord(
        approver_id=approver_id,
        role=role,
        limit=limit,
        delegations=delegations,
        register_version="v2026.08",
        last_updated=AS_OF,
    )


def authority(*extra):
    return RecordTool((*build_case("FIN-001").authority, *extra))


def vendor_record(**overrides):
    return RecordTool(build_case("FIN-001").vendor.model_copy(update=overrides))


def history_record(record_id="H-9000"):
    return HistoryRecord(
        record_id=record_id,
        status="POSTED",
        vendor_id="V-100",
        invoice_no_norm="inv7777",
        currency="AUD",
        gross="100.00",
        invoice_date="2026-09-10",
    )


def recommendation(
    action="POST", amount="9900.00", run_id="run-1", invoice="inv1001", receipters=("rcv-01",)
):
    payload = DecisionPayload(
        case_id="FIN-001",
        vendor_id="V-100",
        invoice_no_norm=invoice,
        amount=amount,
        currency="AUD",
        invoice_date="2026-09-01",
        po_ref="PO-4471",
        outcome=OUTCOMES[action],
        hold_category=None if action == "POST" else "DUPLICATE_RISK",
    )
    return ApprovalRequest(
        run_id=run_id,
        case_id="FIN-001",
        action=action,
        payload=payload,
        vendor_id="V-100",
        vendor_name="Example Supplies Pty Ltd",
        requester="req-01",
        receipters=receipters,
        exceptions=(),
        citations=("FIN-POL-003#s2",),
    )


@dataclass
class Harness:
    service: ApprovalService
    clock: MutableClock
    authority: RecordTool
    vendor: RecordTool
    history: RecordTool
    conn: object

    def audit_events(self):
        return [row[0] for row in self.conn.execute("SELECT event FROM audit ORDER BY id")]


def harness(conn, *extra_approvers):
    clock = MutableClock()
    authority_tool = authority(*extra_approvers)
    vendor_tool = vendor_record()
    history_tool = RecordTool(())
    service = ApprovalService(conn, clock, authority_tool, vendor_tool, history_tool)
    return Harness(service, clock, authority_tool, vendor_tool, history_tool, conn)
