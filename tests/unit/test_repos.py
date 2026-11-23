"""WP 6.B1 repository tests."""

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from finagent.persistence.db import connect, migrate
from finagent.persistence.repos import ApprovalRepo, AuditRepo, CheckpointRepo, RunRepo

NOW = datetime(2026, 9, 15, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "finagent.db")
    migrate(connection, "app")
    yield connection
    connection.close()


def event(**overrides):
    fields = {
        "ts": NOW.isoformat(),
        "run_id": "run-1",
        "event": "tool_attempt",
        "outcome": "ok",
        "duration_ms": 1.5,
    }
    return {**fields, **overrides}


def approval_values(approval_id="a-1", invoice="inv1001", status="PENDING"):
    return {
        "approval_id": approval_id,
        "run_id": "run-1",
        "case_id": "FIN-001",
        "action": "POST",
        "vendor_id": "V-100",
        "vendor_name": "Example Supplies Pty Ltd",
        "invoice_no_norm": invoice,
        "amount": "9900.00",
        "currency": "AUD",
        "payload_json": "{}",
        "payload_hash": "a" * 64,
        "requester": "req-01",
        "receipters_json": '["rcv-01"]',
        "exceptions_json": "[]",
        "citations_json": '["FIN-POL-003#s2"]',
        "snapshot_json": '{"vendor_status": "ACTIVE"}',
        "status": status,
        "requested_at": NOW.isoformat(),
    }


def test_migration_can_run_twice(conn):
    migrate(conn, "app")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"runs", "checkpoints", "audit", "approvals", "escalations"} <= tables


def test_run_round_trip_and_update(conn):
    repo = RunRepo(conn)
    repo.create("run-1", "FIN-001", "RUNNING", "VALIDATE", NOW, NOW)
    repo.update("run-1", "AWAITING_APPROVAL", "REQUEST_APPROVAL", NOW, result_json='{"a": 1}')
    repo.update("run-1", "COMPLETE", "COMPLETE", NOW)
    repo.record_hashes("run-1", "c" * 64, "p" * 64, "f" * 64, "fake-llm")
    row = repo.get("run-1")
    assert (row.status, row.current_node, row.result_json) == ("COMPLETE", "COMPLETE", '{"a": 1}')
    assert (row.as_of, row.model_id) == (NOW, "fake-llm")
    assert repo.get("missing") is None


def test_checkpoints_are_sequenced_per_run(conn):
    runs = RunRepo(conn)
    runs.create("run-1", "FIN-001", "RUNNING", "VALIDATE", NOW, NOW)
    runs.create("run-2", "FIN-002", "RUNNING", "VALIDATE", NOW, NOW)
    repo = CheckpointRepo(conn)
    assert repo.append("run-1", "VALIDATE", "{}", NOW) == 1
    assert repo.append("run-1", "FETCH_INVOICE", '{"x": 1}', NOW) == 2
    assert repo.append("run-2", "VALIDATE", "{}", NOW) == 1
    assert repo.latest("run-1") == (2, "FETCH_INVOICE", '{"x": 1}')
    assert [item[1] for item in repo.all("run-1")] == ["VALIDATE", "FETCH_INVOICE"]
    assert repo.latest("missing") is None


def test_checkpoint_requires_a_run(conn):
    with pytest.raises(sqlite3.IntegrityError):
        CheckpointRepo(conn).append("missing", "VALIDATE", "{}", NOW)


def test_audit_round_trip_flattens_detail(conn):
    repo = AuditRepo(conn)
    repo.append(event(tool="get_vendor_record", attempt=2))
    stored = repo.events("run-1")[0]
    assert (stored["tool"], stored["attempt"], stored["outcome"]) == ("get_vendor_record", 2, "ok")
    assert {"ts", "run_id", "event", "outcome", "duration_ms"} <= set(stored)


def test_audit_detail_is_redacted(conn):
    repo = AuditRepo(conn)
    repo.append(event(note="account 12345678", email="ap.clerk@example.com"))
    stored = str(repo.events("run-1"))
    assert "12345678" not in stored
    assert "ap.clerk" not in stored


def test_audit_is_scoped_to_its_run(conn):
    repo = AuditRepo(conn)
    repo.append(event(run_id="run-1"))
    repo.append(event(run_id="run-2"))
    assert len(repo.events("run-1")) == 1


@pytest.mark.parametrize("statement", ["UPDATE audit SET outcome = 'x'", "DELETE FROM audit"])
def test_audit_update_and_delete_raise(conn, statement):
    repo = AuditRepo(conn)
    repo.append(event())
    with pytest.raises(sqlite3.Error, match="append-only"):
        conn.execute(statement)
    assert len(repo.events("run-1")) == 1


def test_one_pending_request_per_invoice_is_enforced_by_the_database(conn):
    repo = ApprovalRepo(conn)
    repo.insert(approval_values("a-1"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert(approval_values("a-2"))
    repo.insert(approval_values("a-3", invoice="inv2002"))


def test_a_decided_request_allows_a_new_pending_one(conn):
    repo = ApprovalRepo(conn)
    repo.insert(approval_values("a-1"))
    assert repo.decide("a-1", "REJECTED", {"decision": "reject"})
    repo.insert(approval_values("a-2"))
    assert repo.pending_for("V-100", "inv1001").approval_id == "a-2"


def test_decide_is_compare_and_set(conn):
    repo = ApprovalRepo(conn)
    repo.insert(approval_values())
    assert repo.decide("a-1", "APPROVED", {"approver_id": "cc-manager-01"}) is True
    assert repo.decide("a-1", "REJECTED", {"approver_id": "dd-01"}) is False
    record = repo.get("a-1")
    assert (record.status, record.approver_id) == ("APPROVED", "cc-manager-01")


def test_status_is_a_closed_list(conn):
    with pytest.raises(sqlite3.IntegrityError):
        ApprovalRepo(conn).insert(approval_values(status="MAYBE"))


def test_revalidation_is_compare_and_set_from_approved(conn):
    repo = ApprovalRepo(conn)
    repo.insert(approval_values())
    assert repo.mark_revalidated("a-1", False, NOW) is False
    repo.decide("a-1", "APPROVED", {"approver_id": "cc-manager-01"})
    assert repo.mark_revalidated("a-1", False, NOW) is True
    assert repo.get("a-1").status == "INVALIDATED"
    assert repo.mark_revalidated("a-1", True, NOW) is False
    assert repo.get("a-1").status == "INVALIDATED"


def test_record_restores_types(conn):
    repo = ApprovalRepo(conn)
    repo.insert(approval_values())
    record = repo.get("a-1")
    assert record.amount == Decimal("9900.00")
    assert record.receipters == ("rcv-01",)
    assert record.citations == ("FIN-POL-003#s2",)
    assert record.requested_at == NOW
    assert record.snapshot == {"vendor_status": "ACTIVE"}


def test_repeated_escalation_is_recorded_once(conn):
    repo = ApprovalRepo(conn)
    repo.insert(approval_values())
    for _ in range(2):
        repo.add_escalation("a-1", "run-1", "vg-officer-01", "Financial Control", "vendor_creator", NOW)
    escalations = repo.escalations()
    assert len(escalations) == 1
    assert (escalations[0].routed_to, escalations[0].reason) == ("Financial Control", "vendor_creator")
