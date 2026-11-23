# ruff: noqa: I001
"""Run harness for the graph, invariant and resume tests (WP 6.A).

Contract for src/finagent/orchestration:
  graph.NODES; graph.RunState(run_id, signal); graph.next_node(node, state)
    Each node records the spec's `when` value in state.signal.
  runner.build_deps(case_dir, home, clock, llm, max_steps=None, executor=None, crash_hook=None)
    The result exposes .approvals (ApprovalService), .app_db, .sandbox_db (paths) and .max_steps.
  runner.Runner(deps): start() -> view; resume(run_id) -> view
    A view has run_id, status, current_node, outcome, primary_category, visited, result,
    pending_approval_id.
  repos.AuditRepo(conn).events(run_id) -> list of dicts with ts, run_id, event, outcome, duration_ms;
    a tool attempt also has tool and attempt.
"""

import atexit
import json
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

import finagent.tools.factory as tool_factory
from finagent.config.clock import SYDNEY, FixedClock
from finagent.llm.fake import FakeLLM
from finagent.orchestration.graph import RunState
from finagent.orchestration.runner import Runner, build_deps
from finagent.persistence.db import connect
from finagent.persistence.repos import AuditRepo
from fixtures.builder import FAULTS, _raw_case

AS_OF = datetime(2026, 9, 15, 9, 0, tzinfo=SYDNEY)
CLOCK = FixedClock(AS_OF)
RECORD_FILES = ("request", "invoice", "vendor", "po", "history", "authority", "evidence")


class NoSleepExecutor:
    def run(self, fn, timeout_s, on_late):
        return fn()

    def sleep(self, seconds):
        pass


def state_where(node, when):
    """The minimal state in which `node` has produced the signal `when`."""
    return RunState(run_id="run-1", signal=when)


def materialise(case_id, overrides, home):
    """Write a case directory, with overrides applied, that the mock tools can read."""
    raw = _raw_case(case_id, overrides)
    directory = Path(home) / "cases" / case_id
    directory.mkdir(parents=True, exist_ok=True)
    for name in RECORD_FILES:
        (directory / f"{name}.json").write_text(json.dumps(raw[name]), encoding="utf-8")
    meta = {
        "as_of": raw["as_of"],
        "authority_source": raw["authority_source"],
        "faults": FAULTS.get(case_id, {}),
    }
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return directory


def new_home():
    home = Path(tempfile.mkdtemp(prefix="finagent-accept-"))
    atexit.register(shutil.rmtree, home, ignore_errors=True)
    return home


@dataclass
class RunReport:
    status: str
    current_node: str
    outcome: str
    primary_category: str | None
    visited: tuple[str, ...]
    tool_attempts: Counter
    pending_approval: str | None
    audit: list
    run_id: str
    db: Path
    result: object
    deps: object
    runner: object


def report_for(view, deps, runner, home):
    conn = connect(deps.app_db)
    try:
        audit = AuditRepo(conn).events(view.run_id)
    finally:
        conn.close()
    attempts = Counter(event["tool"] for event in audit if "attempt" in event)
    return RunReport(
        view.status,
        view.current_node,
        view.outcome,
        view.primary_category,
        tuple(view.visited),
        attempts,
        view.pending_approval_id,
        audit,
        view.run_id,
        Path(home),
        view.result,
        deps,
        runner,
    )


def apply_decision(report, decision):
    approvals, runner = report.deps.approvals, report.runner
    approval_id = report.pending_approval
    response = approvals.decide(approval_id, decision["approver"], decision["action"], decision.get("reason"))
    view = runner.resume(report.run_id)
    if decision.get("repeat"):
        repeated = approvals.decide(
            approval_id, decision["approver"], decision["action"], decision.get("reason")
        )
        assert repeated == response
        view = runner.resume(report.run_id)
    return report_for(view, report.deps, runner, report.db), response


def run_case(
    case, decision=None, llm=None, overrides=None, max_steps=None, db=None, keep_db=False, crash_hook=None
):
    """Run a case to its pause or end, then apply a scripted decision if given."""
    home = Path(db) if db is not None else new_home()
    case_dir = materialise(case, overrides, home)
    deps = build_deps(
        case_dir,
        home,
        CLOCK,
        llm or FakeLLM("good"),
        max_steps=max_steps,
        executor=NoSleepExecutor(),
        crash_hook=crash_hook,
    )
    runner = Runner(deps)
    report = report_for(runner.start(), deps, runner, home)
    if decision is not None:
        report, _ = apply_decision(report, decision)
    return report


def counts(report):
    conn = connect(report.deps.sandbox_db)
    try:
        block = conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        ledger = dict(conn.execute("SELECT action, COUNT(*) FROM ledger GROUP BY action").fetchall())
    finally:
        conn.close()
    return {"block": block, "ledger": ledger}


@pytest.fixture
def spy_submit(monkeypatch):
    """Records every submit call and still performs it."""
    calls = []
    real = tool_factory.submit_finance_decision

    def spy(conn, request, approvals, clock, audit=None):
        calls.append(request)
        return real(conn, request, approvals, clock, audit=audit)

    monkeypatch.setattr(tool_factory, "submit_finance_decision", spy)
    return calls
