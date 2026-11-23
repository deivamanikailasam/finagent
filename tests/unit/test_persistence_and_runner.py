"""WP 4.B unit tests for behaviour the acceptance tests do not reach."""

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from finagent.config.clock import FixedClock
from finagent.persistence.db import connect, migrate, transaction
from finagent.tools.block_submit import Denied, DuplicateRisk
from finagent.tools.declarations import load_tool_declarations
from finagent.tools.runner import RunContext, ThreadExecutor, ToolRunner
from finagent.tools.schemas import BlockPayload, payload_hash_of

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_tool_declarations(ROOT / "config/tools.yaml")
BODY = BlockPayload(case_id="FIN-001", vendor_id="V-100", invoice_no_norm="inv1001")
BLOCK_ARGS = {
    "run_id": "run-1",
    "action": "BLOCK",
    "rule_id": "TOL-GOODS",
    "authorisation": {"type": "SYSTEM_FAILSAFE", "id": "auth-1"},
    "payload": BODY.model_dump(mode="json"),
    "payload_hash": payload_hash_of(BODY),
    "idempotency_key": "a" * 64,
}


def runner_for(tool, events):
    context = RunContext("run-1", "FIN-001", events.append, FixedClock(datetime(2026, 9, 15, tzinfo=UTC)))
    return ToolRunner({"place_payment_block": tool}, CONFIG, ThreadExecutor(), context)


def test_every_connection_has_the_required_pragmas(tmp_path):
    conn = connect(tmp_path / "x.db")
    assert conn.isolation_level is None
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 2000
    conn.close()


def test_transaction_rolls_back_on_error(tmp_path):
    conn = connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE t (v)")
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute("INSERT INTO t VALUES (1)")
        raise RuntimeError
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
    conn.close()


def test_migration_can_run_twice(tmp_path):
    conn = connect(tmp_path / "x.db")
    migrate(conn, "sandbox")
    migrate(conn, "sandbox")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"ledger", "ledger_lines", "blocks", "idempotency"} <= tables
    conn.close()


def test_unknown_migration_kind_is_rejected(tmp_path):
    conn = connect(tmp_path / "x.db")
    with pytest.raises(ValueError):
        migrate(conn, "../other")
    conn.close()


def test_line_row_must_reference_a_ledger_row(tmp_path):
    conn = connect(tmp_path / "x.db")
    migrate(conn, "sandbox")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO ledger_lines (ledger_id, run_id, po_line_ref, qty) VALUES (99, 'r', 'L', 1)"
        )
    conn.close()


@pytest.mark.parametrize("error", [Denied("no"), DuplicateRisk("DUPLICATE_RISK")])
def test_a_definite_refusal_is_raised_and_never_submit_unknown(error):
    calls = []

    def tool(_request):
        calls.append(1)
        raise error

    events = []
    with pytest.raises(type(error)):
        runner_for(tool, events).call("place_payment_block", BLOCK_ARGS)
    assert len(calls) == 1
    assert events[-1]["outcome"] == type(error).__name__


def test_thread_executor_returns_a_value():
    assert ThreadExecutor().run(lambda: 5, 1.0, lambda _value: None) == 5


def test_thread_executor_propagates_errors():
    def fail():
        raise ValueError("bad")

    with pytest.raises(ValueError, match="bad"):
        ThreadExecutor().run(fail, 1.0, lambda _value: None)


def test_thread_executor_times_out_and_reports_the_late_result():
    release = threading.Event()
    late = threading.Event()
    received = []

    def slow():
        release.wait(5)
        return "late"

    def on_late(value):
        received.append(value)
        late.set()

    with pytest.raises(TimeoutError):
        ThreadExecutor().run(slow, 0.01, on_late)
    release.set()
    assert late.wait(5)
    assert received == ["late"]


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": "run-2"},
        {
            "payload": {**BLOCK_ARGS["payload"], "case_id": "FIN-002"},
            "payload_hash": payload_hash_of(BODY.model_copy(update={"case_id": "FIN-002"})),
        },
    ],
)
def test_a_write_outside_the_run_scope_never_reaches_the_tool(changes):
    calls = []
    result = runner_for(lambda request: calls.append(request), []).call(
        "place_payment_block", {**BLOCK_ARGS, **changes}
    )
    assert calls == []
    assert (result.status, result.unknown.reason) == ("unknown", "outside_run_scope")


def test_an_audit_failure_does_not_lose_a_committed_result():
    def broken_audit(_event):
        raise RuntimeError("audit store down")

    context = RunContext("run-1", "FIN-001", broken_audit, FixedClock(datetime(2026, 9, 15, tzinfo=UTC)))
    runner = ToolRunner(
        {"place_payment_block": lambda _request: {"status": "BLOCKED"}}, CONFIG, ThreadExecutor(), context
    )
    assert runner.call("place_payment_block", BLOCK_ARGS).status == "ok"
