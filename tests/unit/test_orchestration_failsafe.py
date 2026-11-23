"""Fail-safe paths of the run graph that the acceptance scenarios do not reach."""

import json

import pytest

import finagent.tools.factory as tool_factory
from finagent.orchestration import nodes
from finagent.orchestration.checkpoint import resume
from finagent.persistence.db import connect
from finagent.persistence.repos import CheckpointRepo, RunRepo
from finagent.rag.retrieve import RetrievalDenied, Retriever
from tests.acceptance.support_graph import counts, new_home, run_case
from tests.acceptance.support_resilience import SimulatedCrash, make_workspace, restart


def test_block_found_by_reconciliation_is_reported(monkeypatch):
    real = tool_factory.place_payment_block

    def commit_then_lose_response(*args, **kwargs):
        real(*args, **kwargs)
        raise ConnectionError("response lost")

    monkeypatch.setattr(tool_factory, "place_payment_block", commit_then_lose_response)
    report = run_case("FIN-004")

    assert report.visited[-3:] == ("SUBMIT_UNKNOWN", "PLACE_BLOCK", "REQUEST_APPROVAL")
    blocks = [item for item in report.result.actions_taken if item.action == "BLOCK"]
    assert len(blocks) == 1
    assert blocks[0].posting_ref.startswith("BLOCK-")
    assert counts(report)["block"] == 1


def test_denied_rule_source_fails_the_run_without_writing(monkeypatch):
    def deny(*_args, **_kwargs):
        raise RetrievalDenied("denied")

    monkeypatch.setattr(Retriever, "fetch_by_id", deny)
    report = run_case("FIN-001")

    assert report.status == "FAILED"
    assert report.pending_approval is None
    assert counts(report) == {"block": 0, "ledger": {}}


def test_block_authorisation_is_checkpointed_before_the_write(monkeypatch):
    home = new_home()
    real = tool_factory.place_payment_block
    saved = []

    def spy(conn, request, clock, audit=None):
        app = connect(home / "finagent.db")
        try:
            latest = CheckpointRepo(app).latest(request.run_id)
        finally:
            app.close()
        saved.append((json.loads(latest[2])["block_authorisation_id"], request.authorisation.id))
        return real(conn, request, clock, audit=audit)

    monkeypatch.setattr(tool_factory, "place_payment_block", spy)
    run_case("FIN-004", db=home)

    assert len(saved) == 1
    assert saved[0][0] == saved[0][1]


def test_run_records_what_it_was_decided_against():
    report = run_case("FIN-001")
    conn = connect(report.deps.app_db)
    try:
        row = RunRepo(conn).get(report.run_id)
    finally:
        conn.close()

    assert all(len(value) == 64 for value in (row.corpus_hash, row.prompt_hash, row.config_hash))
    assert row.model_id


def test_recorded_model_reply_is_not_bought_again_after_a_crash(monkeypatch):
    real_route = nodes.HANDLERS["ROUTE"]
    state = {"crashed": False}

    def crash_once(run_state, context):
        if not state["crashed"]:
            state["crashed"] = True
            raise SimulatedCrash("ROUTE")
        real_route(run_state, context)

    monkeypatch.setitem(nodes.HANDLERS, "ROUTE", crash_once)
    home = make_workspace()
    deps, runner = restart("FIN-001", home)
    with pytest.raises(SimulatedCrash):
        runner.start()
    run_id = deps.conn.execute("SELECT run_id FROM runs").fetchone()[0]

    deps, _ = restart("FIN-001", home)
    calls = []
    real_llm = deps.llm

    class CountingLLM:
        def generate_rationale(self, request):
            calls.append(request)
            return real_llm.generate_rationale(request)

    deps.llm = CountingLLM()
    view = resume(run_id, deps)

    assert view.status == "AWAITING_APPROVAL"
    assert calls == []
