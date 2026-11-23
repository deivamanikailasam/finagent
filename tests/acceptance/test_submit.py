# ruff: noqa: I001
"""Acceptance tests for src/finagent/tools/block_submit.py (WP 4.A). Permissions come from the spec."""

import hashlib
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from finagent.config.clock import FixedClock
from finagent.domain.enums import ApprovalStatus, AuthorisationType
from finagent.tools import block_submit
from finagent.tools.block_submit import (
    Denied,
    DuplicateRisk,
    IdempotencyConflict,
    block_authorisation_id,
    derive_key,
    place_payment_block,
    submit_finance_decision,
)
from tests.acceptance.support import approval, block_input, count, decision_payload, sandbox, sql  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
SPEC = yaml.safe_load((ROOT / "spec/submit_permissions.yaml").read_text(encoding="utf-8"))
SUBMIT_RULES = SPEC["tools"]["submit_finance_decision"]["actions"]
CLOCK = FixedClock(datetime(2026, 9, 15, tzinfo=UTC))
MATRIX = [
    (action, status.value, auth.value)
    for action in SUBMIT_RULES
    for status in ApprovalStatus
    for auth in AuthorisationType
]


def submit(sb, request, audit=None):
    return submit_finance_decision(sb.conn, request, sb.approvals, CLOCK, audit=audit)


def block(sb, request, audit=None):
    return place_payment_block(sb.conn, request, CLOCK, audit=audit)


def test_matrix_covers_every_combination():
    assert len(MATRIX) == 4 * 4 * 2


@pytest.mark.parametrize(("action", "status", "auth"), MATRIX)
def test_permission_matrix_follows_the_spec(sandbox, action, status, auth):  # noqa: F811
    rule = SUBMIT_RULES[action]
    allowed = (
        auth == rule["requires"] and status == rule["approval_state"] and status not in rule["never_state"]
    )
    request = approval(sandbox, action=action, status=status, auth=auth)
    if allowed:
        assert submit(sandbox, request).status == "COMMITTED"
        assert count(sandbox, "ledger") == 1
    else:
        with pytest.raises(Denied):
            submit(sandbox, request)
        assert count(sandbox, "ledger") == 0


@pytest.mark.parametrize("action", list(SUBMIT_RULES))
def test_system_failsafe_can_never_submit(sandbox, action):  # noqa: F811
    with pytest.raises(Denied):
        submit(sandbox, approval(sandbox, action=action, auth="SYSTEM_FAILSAFE"))
    assert count(sandbox, "ledger") == 0


@pytest.mark.parametrize(
    "binding",
    [{"bound_run_id": "run-2"}, {"bound_payload_hash": "b" * 64}, {"bound_action": "HOLD"}],
)
def test_approval_bound_to_something_else_is_denied(sandbox, binding):  # noqa: F811
    with pytest.raises(Denied):
        submit(sandbox, approval(sandbox, **binding))
    assert count(sandbox, "ledger") == 0


def test_missing_approval_is_denied(sandbox):  # noqa: F811
    request = approval(sandbox)
    sandbox.approvals.records.clear()
    with pytest.raises(Denied):
        submit(sandbox, request)


def test_unknown_submit_action_is_denied(sandbox):  # noqa: F811
    request = approval(sandbox).model_copy(update={"action": "RELEASE"})
    with pytest.raises(Denied):
        submit(sandbox, request)


@pytest.mark.parametrize("action", ["POST", "RELEASE", "HOLD", "REJECT"])
def test_block_tool_rejects_any_action_but_block(sandbox, action):  # noqa: F811
    with pytest.raises(Denied):
        block(sandbox, block_input(action=action))
    assert count(sandbox, "blocks") == 0


def test_block_requires_system_failsafe(sandbox):  # noqa: F811
    with pytest.raises(Denied):
        block(sandbox, block_input(auth="HUMAN_APPROVAL"))
    assert count(sandbox, "blocks") == 0


def test_block_authorisation_must_be_bound_to_run_and_rule(sandbox):  # noqa: F811
    with pytest.raises(Denied):
        block(sandbox, block_input(auth_id=block_authorisation_id("run-2", "TOL-GOODS")))
    assert count(sandbox, "blocks") == 0


def test_block_key_must_match(sandbox):  # noqa: F811
    with pytest.raises(Denied):
        block(sandbox, block_input(key="c" * 64))
    assert count(sandbox, "blocks") == 0


def test_key_derivation_matches_the_spec_formula():
    expected = hashlib.sha256(b"run-1|approval-1|POST").hexdigest()
    assert derive_key("run-1", "approval-1", "POST") == expected


def test_block_authorisation_id_is_deterministic():
    expected = hashlib.sha256(b"run-1|TOL-GOODS|BLOCK").hexdigest()
    assert block_authorisation_id("run-1", "TOL-GOODS") == expected
    assert block_authorisation_id("run-1", "TOL-GOODS") == block_authorisation_id("run-1", "TOL-GOODS")
    assert block_authorisation_id("run-1", "TOL-GOODS") != block_authorisation_id("run-2", "TOL-GOODS")


def test_wrong_idempotency_key_is_denied(sandbox):  # noqa: F811
    with pytest.raises(Denied):
        submit(sandbox, approval(sandbox, key="c" * 64))
    assert count(sandbox, "ledger") == 0


def test_same_key_and_payload_replays_the_stored_response(sandbox):  # noqa: F811
    request = approval(sandbox)
    first = submit(sandbox, request)
    second = submit(sandbox, request)
    assert second == first
    assert count(sandbox, "ledger") == 1
    assert count(sandbox, "idempotency") == 1


def test_same_key_with_a_different_payload_is_a_conflict(sandbox):  # noqa: F811
    submit(sandbox, approval(sandbox))
    changed = approval(sandbox, payload=decision_payload(amount="9901.00"))
    assert changed.idempotency_key == derive_key("run-1", "approval-1", "POST")
    with pytest.raises(IdempotencyConflict):
        submit(sandbox, changed)
    assert count(sandbox, "ledger") == 1


def test_failure_between_ledger_and_idempotency_leaves_neither(sandbox, monkeypatch):  # noqa: F811
    def fail(*_args, **_kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(block_submit, "_after_ledger_insert", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        submit(sandbox, approval(sandbox))
    assert count(sandbox, "ledger") == 0
    assert count(sandbox, "ledger_lines") == 0
    assert count(sandbox, "idempotency") == 0


def test_retry_after_a_failed_transaction_commits_once(sandbox, monkeypatch):  # noqa: F811
    request = approval(sandbox)

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(block_submit, "_after_ledger_insert", fail)
    with pytest.raises(RuntimeError):
        submit(sandbox, request)
    monkeypatch.undo()
    assert submit(sandbox, request).status == "COMMITTED"
    assert count(sandbox, "ledger") == 1


def test_second_run_posting_the_same_invoice_is_duplicate_risk(sandbox):  # noqa: F811
    submit(sandbox, approval(sandbox))
    second = approval(sandbox, run_id="run-2", approval_id="approval-2")
    with pytest.raises(DuplicateRisk):
        submit(sandbox, second)
    assert count(sandbox, "ledger") == 1
    assert count(sandbox, "idempotency") == 1


def test_blocking_twice_in_one_run_is_one_row(sandbox):  # noqa: F811
    first = block(sandbox, block_input())
    second = block(sandbox, block_input())
    assert second == first
    assert count(sandbox, "blocks") == 1


@pytest.mark.parametrize("rule_id", ["APPROVER_REJECT", "REVALIDATION", "UNIQUE_VIOLATION"])
def test_second_block_for_a_run_returns_the_existing_block(sandbox, rule_id):  # noqa: F811
    first = block(sandbox, block_input())
    second = block(sandbox, block_input(rule_id=rule_id))
    assert second == first
    assert count(sandbox, "blocks") == 1
    assert sql(sandbox, "SELECT rule_id FROM blocks") == [("TOL-GOODS",)]


def test_block_records_the_invoice_key(sandbox):  # noqa: F811
    block(sandbox, block_input())
    assert sql(sandbox, "SELECT run_id, action, invoice_key FROM blocks") == [
        ("run-1", "BLOCK", "V-100|inv1001")
    ]


def test_post_is_denied_on_an_invoice_blocked_by_the_same_run(sandbox):  # noqa: F811
    block(sandbox, block_input())
    with pytest.raises(Denied):
        submit(sandbox, approval(sandbox))
    assert count(sandbox, "ledger") == 0


def test_post_is_denied_on_an_invoice_blocked_by_another_run(sandbox):  # noqa: F811
    block(sandbox, block_input(run_id="run-A"))
    with pytest.raises(Denied):
        submit(sandbox, approval(sandbox, run_id="run-B"))
    assert count(sandbox, "ledger") == 0


@pytest.mark.parametrize("action", ["REJECT", "HOLD", "ESCALATE"])
def test_protective_decisions_are_allowed_on_a_blocked_invoice(sandbox, action):  # noqa: F811
    block(sandbox, block_input())
    assert submit(sandbox, approval(sandbox, action=action)).status == "COMMITTED"
    assert sql(sandbox, "SELECT action FROM ledger") == [(action,)]


def test_block_on_another_invoice_does_not_stop_a_post(sandbox):  # noqa: F811
    block(sandbox, block_input())
    other = approval(sandbox, payload=decision_payload(invoice_no_norm="inv2002"))
    assert submit(sandbox, other).status == "COMMITTED"


@pytest.mark.parametrize("table", ["ledger", "blocks", "ledger_lines"])
@pytest.mark.parametrize("statement", ["UPDATE {table} SET run_id = 'x'", "DELETE FROM {table}"])
def test_ledger_and_blocks_are_append_only(sandbox, table, statement):  # noqa: F811
    submit(sandbox, approval(sandbox, action="HOLD"))
    block(sandbox, block_input())
    before = count(sandbox, table)
    assert before == 2 if table == "ledger_lines" else before == 1
    with pytest.raises(sqlite3.Error):
        sandbox.conn.execute(statement.format(table=table))
    assert count(sandbox, table) == before


def test_amount_is_stored_as_text_and_returned_as_decimal(sandbox):  # noqa: F811
    output = submit(sandbox, approval(sandbox))
    assert sql(sandbox, "SELECT typeof(amount), amount FROM ledger") == [("text", "9900.00")]
    assert output.amount == Decimal("9900.00")
    assert isinstance(output.amount, Decimal)


def test_posting_ref_is_returned_stored_and_audited(sandbox):  # noqa: F811
    events = []
    output = submit(sandbox, approval(sandbox), audit=events.append)
    assert output.posting_ref
    assert sql(sandbox, "SELECT posting_ref FROM ledger") == [(output.posting_ref,)]
    assert any(event.get("posting_ref") == output.posting_ref for event in events)
    assert "9900.00" not in str([event.get("payload") for event in events])


def test_ledger_stores_the_fields_history_needs(sandbox):  # noqa: F811
    payload = decision_payload("ESCALATE", hold_category="BANK_CHANGE")
    submit(sandbox, approval(sandbox, action="ESCALATE", payload=payload))
    assert sql(sandbox, "SELECT invoice_date, po_ref, prior_outcome, hold_category FROM ledger") == [
        ("2026-09-01", "PO-4471", "ESCALATE_CONTROL_REVIEW", "BANK_CHANGE")
    ]


def test_ledger_stores_one_row_per_po_line(sandbox):  # noqa: F811
    submit(sandbox, approval(sandbox))
    rows = sql(sandbox, "SELECT po_ref, po_line_ref, qty FROM ledger_lines ORDER BY po_line_ref")
    assert rows == [("PO-4471", "PO-L1", 40), ("PO-4471", "PO-L2", 100)]


def test_denied_submit_writes_no_line_rows(sandbox):  # noqa: F811
    with pytest.raises(Denied):
        submit(sandbox, approval(sandbox, status="PENDING"))
    assert count(sandbox, "ledger_lines") == 0
