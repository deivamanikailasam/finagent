# ruff: noqa: I001
"""Crash and restart support for the resume tests (WP 6.A).

Contract for src/finagent/orchestration/checkpoint.py:
  resume(run_id, deps) -> view: replays recorded results for nodes before the pause and runs
  REVALIDATE, PLACE_BLOCK and SUBMIT live.
The runner calls deps.crash_hook(point) at each named crash point, including
"after_submit_before_checkpoint". SimulatedCrash derives from BaseException so no handler can absorb it.
"""

from finagent.config.clock import FixedClock
from finagent.llm.fake import FakeLLM
from finagent.orchestration.runner import Runner, build_deps
from finagent.persistence.db import connect
from tests.acceptance.support_graph import AS_OF, NoSleepExecutor, materialise, new_home, report_for, run_case

AFTER_SUBMIT = "after_submit_before_checkpoint"


class SimulatedCrash(BaseException):
    """The process died at this point."""


def crash_at(point):
    """A crash hook that fails once, the first time the run reaches this point."""
    state = {"fired": False}

    def hook(name):
        if name == point and not state["fired"]:
            state["fired"] = True
            raise SimulatedCrash(point)

    return hook


def make_workspace(case_id="FIN-001", overrides=None):
    home = new_home()
    materialise(case_id, overrides, home)
    return home


def restart(case_id, home, crash_hook=None):
    """New dependencies over the same files, as after a process restart."""
    case_dir = home / "cases" / case_id
    deps = build_deps(
        case_dir,
        home,
        FixedClock(AS_OF),
        FakeLLM("good"),
        executor=NoSleepExecutor(),
        crash_hook=crash_hook,
    )
    return deps, Runner(deps)


def start(case_id="FIN-001", home=None, crash_hook=None):
    home = home or make_workspace(case_id)
    deps, runner = restart(case_id, home, crash_hook)
    return report_for(runner.start(), deps, runner, home)


def decide(report, action="approve", approver="cc-manager-01", reason=None):
    return report.deps.approvals.decide(report.pending_approval, approver, action, reason)


def rows(db):
    conn = connect(db)
    try:
        return {
            "block": conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0],
            "ledger": conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0],
            "idempotency": conn.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0],
        }
    finally:
        conn.close()


__all__ = [
    "AFTER_SUBMIT",
    "SimulatedCrash",
    "crash_at",
    "decide",
    "make_workspace",
    "restart",
    "rows",
    "run_case",
    "start",
]
