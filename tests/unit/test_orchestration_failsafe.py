"""Fail-safe paths of the run graph that the acceptance scenarios do not reach."""

import json

import finagent.tools.factory as tool_factory
from finagent.persistence.db import connect
from finagent.persistence.repos import CheckpointRepo
from finagent.rag.retrieve import RetrievalDenied, Retriever
from tests.acceptance.support_graph import counts, new_home, run_case


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
