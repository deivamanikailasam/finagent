# ruff: noqa: I001
"""Shared fixtures for the block and submit acceptance tests (WP 4.A)."""

from dataclasses import dataclass, field

import pytest

from finagent.persistence.db import connect, migrate
from finagent.tools.block_submit import ApprovalView, block_authorisation_id, derive_key
from finagent.tools.schemas import (
    BlockPayload,
    DecisionPayload,
    PlacePaymentBlockInput,
    SubmitFinanceDecisionInput,
    payload_hash_of,
)

OUTCOME_FOR_ACTION = {
    "POST": "APPROVE_FOR_POSTING",
    "HOLD": "HOLD_FOR_INFORMATION",
    "ESCALATE": "ESCALATE_CONTROL_REVIEW",
    "REJECT": "REJECT_DUPLICATE",
}


class FakeApprovals:
    """The ApprovalLookup port: get(approval_id) returns an ApprovalView or None."""

    def __init__(self):
        self.records = {}

    def get(self, approval_id):
        return self.records.get(approval_id)


@dataclass
class Sandbox:
    conn: object
    approvals: FakeApprovals = field(default_factory=FakeApprovals)


@pytest.fixture
def sandbox(tmp_path):
    conn = connect(tmp_path / "erp_sandbox.db")
    migrate(conn, "sandbox")
    yield Sandbox(conn)
    conn.close()


def decision_payload(action="POST", **overrides):
    fields = {
        "case_id": "FIN-001",
        "vendor_id": "V-100",
        "invoice_no_norm": "inv1001",
        "amount": "9900.00",
        "currency": "AUD",
        "invoice_date": "2026-09-01",
        "po_ref": "PO-4471",
        "outcome": OUTCOME_FOR_ACTION[action],
        "hold_category": None if action == "POST" else "DUPLICATE_RISK",
        "line_quantities": [{"po_line_ref": "PO-L1", "qty": 40}, {"po_line_ref": "PO-L2", "qty": 100}],
    }
    return DecisionPayload(**{**fields, **overrides})


def approval(
    sb,
    run_id="run-1",
    action="POST",
    status="APPROVED",
    auth="HUMAN_APPROVAL",
    bound_run_id=None,
    bound_payload_hash=None,
    bound_action=None,
    payload=None,
    approval_id="approval-1",
    key=None,
):
    """Record an approval and build the matching submit input."""
    body = payload or decision_payload(action)
    payload_hash = payload_hash_of(body)
    sb.approvals.records[approval_id] = ApprovalView(
        approval_id=approval_id,
        run_id=bound_run_id or run_id,
        payload_hash=bound_payload_hash or payload_hash,
        action=bound_action or action,
        status=status,
    )
    return SubmitFinanceDecisionInput(
        run_id=run_id,
        action=action,
        authorisation={"type": auth, "id": approval_id},
        payload=body,
        payload_hash=payload_hash,
        idempotency_key=key or derive_key(run_id, approval_id, action),
    )


def block_input(
    run_id="run-1", rule_id="TOL-GOODS", action="BLOCK", auth="SYSTEM_FAILSAFE", auth_id=None, key=None
):
    body = BlockPayload(case_id="FIN-001", vendor_id="V-100", invoice_no_norm="inv1001")
    authorisation_id = auth_id or block_authorisation_id(run_id, rule_id)
    return PlacePaymentBlockInput(
        run_id=run_id,
        action=action,
        rule_id=rule_id,
        authorisation={"type": auth, "id": authorisation_id},
        payload=body,
        payload_hash=payload_hash_of(body),
        idempotency_key=key or derive_key(run_id, authorisation_id, "BLOCK"),
    )


def count(sb, table):
    return sb.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def sql(sb, statement, *parameters):
    return sb.conn.execute(statement, parameters).fetchall()
