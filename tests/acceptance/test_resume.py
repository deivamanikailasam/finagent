# ruff: noqa: I001
"""Crash recovery for orchestration/checkpoint.py (WP 6.A). Every crash test asserts row counts."""

import json

import pytest

import finagent.tools.factory as tool_factory
from finagent.orchestration.checkpoint import resume
from tests.acceptance.support_graph import report_for, spy_submit  # noqa: F401
from tests.acceptance.support_resilience import (
    AFTER_SUBMIT,
    SimulatedCrash,
    crash_at,
    decide,
    make_workspace,
    restart,
    rows,
    start,
)


def recover(case_id, home, run_id):
    deps, runner = restart(case_id, home)
    return report_for(resume(run_id, deps), deps, runner, home), deps


def tool_calls(report):
    return sum(report.tool_attempts.values())


def test_crash_after_submit_recovers_to_one_ledger_row():
    home = make_workspace()
    paused = start(home=home, crash_hook=crash_at(AFTER_SUBMIT))
    decide(paused)
    with pytest.raises(SimulatedCrash):
        paused.runner.resume(paused.run_id)
    assert rows(paused.deps.sandbox_db) == {"block": 0, "ledger": 1, "idempotency": 1}
    report, deps = recover("FIN-001", home, paused.run_id)
    assert (report.status, report.outcome) == ("COMPLETE", paused.outcome)
    assert rows(deps.sandbox_db) == {"block": 0, "ledger": 1, "idempotency": 1}


def test_recovering_twice_changes_nothing():
    home = make_workspace()
    paused = start(home=home, crash_hook=crash_at(AFTER_SUBMIT))
    decide(paused)
    with pytest.raises(SimulatedCrash):
        paused.runner.resume(paused.run_id)
    first, _ = recover("FIN-001", home, paused.run_id)
    second, deps = recover("FIN-001", home, paused.run_id)
    assert (second.status, second.result) == (first.status, first.result)
    assert rows(deps.sandbox_db)["ledger"] == 1


def test_paused_run_survives_a_restart_and_posts_exactly_once():
    home = make_workspace()
    paused = start(home=home)
    deps, runner = restart("FIN-001", home)
    decision = deps.approvals.decide(paused.pending_approval, "cc-manager-01", "approve")
    assert decision.code == "APPROVED"
    finished = report_for(resume(paused.run_id, deps), deps, runner, home)
    assert finished.status == "COMPLETE"
    assert rows(deps.sandbox_db) == {"block": 0, "ledger": 1, "idempotency": 1}


def test_a_paused_run_stays_paused_on_resume():
    home = make_workspace()
    paused = start(home=home)
    report, deps = recover("FIN-001", home, paused.run_id)
    assert (report.status, report.current_node) == ("AWAITING_APPROVAL", "REQUEST_APPROVAL")
    assert report.pending_approval == paused.pending_approval
    assert rows(deps.sandbox_db) == {"block": 0, "ledger": 0, "idempotency": 0}


def test_replay_before_the_pause_makes_no_tool_calls():
    home = make_workspace()
    paused = start(home=home)
    report, _ = recover("FIN-001", home, paused.run_id)
    assert tool_calls(report) == tool_calls(paused)


def test_revalidate_and_submit_run_live_after_the_pause(spy_submit):  # noqa: F811
    home = make_workspace()
    paused = start(home=home)
    deps, runner = restart("FIN-001", home)
    deps.approvals.decide(paused.pending_approval, "cc-manager-01", "approve")
    finished = report_for(resume(paused.run_id, deps), deps, runner, home)
    assert {"REVALIDATE", "SUBMIT"} <= set(finished.visited)
    assert len(spy_submit) == 1


def test_block_is_not_placed_twice_when_a_blocked_run_resumes():
    home = make_workspace("FIN-002")
    paused = start("FIN-002", home=home)
    assert rows(paused.deps.sandbox_db)["block"] == 1
    report, deps = recover("FIN-002", home, paused.run_id)
    assert report.status == "AWAITING_APPROVAL"
    assert rows(deps.sandbox_db)["block"] == 1


def timeout_after(monkeypatch, commit_first):
    """The first submit times out, either after committing or without writing."""
    real = tool_factory.submit_finance_decision
    state = {"calls": 0}

    def flaky(conn, request, approvals, clock, audit=None):
        state["calls"] += 1
        if state["calls"] == 1:
            if commit_first:
                real(conn, request, approvals, clock, audit=audit)
            raise TimeoutError
        return real(conn, request, approvals, clock, audit=audit)

    monkeypatch.setattr(tool_factory, "submit_finance_decision", flaky)
    return state


def test_submit_unknown_with_a_committed_row_completes_without_resubmitting(monkeypatch):
    state = timeout_after(monkeypatch, commit_first=True)
    paused = start()
    decide(paused)
    view = paused.runner.resume(paused.run_id)
    assert view.status == "COMPLETE"
    assert "SUBMIT_UNKNOWN" in view.visited
    assert state["calls"] == 1
    assert rows(paused.deps.sandbox_db) == {"block": 0, "ledger": 1, "idempotency": 1}


def test_submit_unknown_without_a_row_revalidates_and_retries_with_the_same_key(monkeypatch, spy_submit):  # noqa: F811
    state = timeout_after(monkeypatch, commit_first=False)
    paused = start()
    decide(paused)
    view = paused.runner.resume(paused.run_id)
    assert view.status == "COMPLETE"
    assert state["calls"] == 2
    visited = list(view.visited)
    unknown_at = visited.index("SUBMIT_UNKNOWN")
    assert visited[unknown_at + 1] == "REVALIDATE"
    assert rows(paused.deps.sandbox_db) == {"block": 0, "ledger": 1, "idempotency": 1}


def test_hold_retry_after_submit_unknown_goes_straight_to_submit(monkeypatch):
    state = timeout_after(monkeypatch, commit_first=False)
    home = make_workspace("FIN-004")
    paused = start("FIN-004", home=home)
    decide(paused)
    view = paused.runner.resume(paused.run_id)
    assert view.status == "COMPLETE"
    assert state["calls"] == 2
    visited = list(view.visited)
    assert visited[visited.index("SUBMIT_UNKNOWN") + 1] == "SUBMIT"
    assert rows(paused.deps.sandbox_db) == {"block": 1, "ledger": 1, "idempotency": 1}


def test_a_committed_row_is_found_even_when_the_budget_is_spent(monkeypatch):
    timeout_after(monkeypatch, commit_first=True)
    paused = start()
    paused.deps.max_steps = len(paused.visited) + 3  # REVALIDATE, SUBMIT and SUBMIT_UNKNOWN only
    decide(paused)
    view = paused.runner.resume(paused.run_id)
    assert view.status == "COMPLETE"
    assert rows(paused.deps.sandbox_db)["ledger"] == 1


def test_duplicate_callback_posts_once():
    paused = start()
    first = decide(paused)
    paused.runner.resume(paused.run_id)
    second = decide(paused)
    view = paused.runner.resume(paused.run_id)
    assert second == first
    assert view.status == "COMPLETE"
    assert rows(paused.deps.sandbox_db)["ledger"] == 1


def test_two_runs_for_one_invoice_have_one_pending_request():
    home = make_workspace()
    first = start(home=home)
    second = start(home=home)
    assert second.pending_approval == first.pending_approval
    assert second.run_id != first.run_id


def test_reject_leaves_one_block_and_no_ledger_row():
    paused = start()
    decide(paused, "reject", reason="not ours")
    view = paused.runner.resume(paused.run_id)
    assert view.status == "REJECTED_BY_APPROVER"
    assert rows(paused.deps.sandbox_db) == {"block": 1, "ledger": 0, "idempotency": 0}


def test_an_invalidated_approval_cannot_post():
    home = make_workspace()
    paused = start(home=home)
    decide(paused)
    vendor_file = home / "cases" / "FIN-001" / "vendor.json"
    vendor = json.loads(vendor_file.read_text(encoding="utf-8"))
    vendor_file.write_text(json.dumps({**vendor, "status": "BLOCKED"}), encoding="utf-8")
    view = paused.runner.resume(paused.run_id)
    assert view.status == "AWAITING_APPROVAL"
    assert view.primary_category == "VENDOR_BLOCK"
    assert paused.deps.approvals.get(paused.pending_approval).status == "INVALIDATED"
    assert rows(paused.deps.sandbox_db) == {"block": 1, "ledger": 0, "idempotency": 0}


def test_fin_001_started_twice_posts_once():
    home = make_workspace()
    first = start(home=home)
    second = start(home=home)
    decide(first)
    assert first.runner.resume(first.run_id).status == "COMPLETE"
    second.runner.resume(second.run_id)
    assert _post_rows(first) == 1


def _post_rows(report):
    from finagent.persistence.db import connect

    conn = connect(report.deps.sandbox_db)
    try:
        return conn.execute("SELECT COUNT(*) FROM ledger WHERE action = 'POST'").fetchone()[0]
    finally:
        conn.close()
