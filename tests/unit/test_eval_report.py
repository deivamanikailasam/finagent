"""WP 8.2: comparison, reporting and the live harness, with stubbed runs and no real model."""

import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from finagent.cli.main import app
from finagent.config.clock import FixedClock
from finagent.controls.decision import evaluate
from finagent.eval.live import exception_coverage, run_live, write_model_section
from finagent.eval.report import (
    OFFLINE_LABEL,
    build_payload,
    list_runs,
    render_table,
    store_payload,
    write_samples,
)
from finagent.eval.scenarios import (
    NO_CHECK,
    Assertion,
    CaseResult,
    Observation,
    compare,
    is_cited,
    load_expectations,
    run_case,
    run_scenarios,
)
from finagent.llm.fake import FakeLLM
from finagent.llm.prompt import PromptChunk, RationaleInput
from fixtures.builder import build_case

NOW = datetime(2026, 11, 23, 4, 0, tzinfo=UTC)
EXPECTED = {
    "outcome": "HOLD_FOR_INFORMATION",
    "primary_category": "MISSING_RECEIPT",
    "owner": "AP Operations",
    "run_status_before_decision": "AWAITING_APPROVAL",
    "rows_before_decision": {"block": 1, "ledger": {}},
    "decision": {"action": "approve", "approver": "cc-manager-01"},
    "run_status_after_decision": "COMPLETE",
    "rows_after_decision": {"block": 1, "ledger": {"HOLD": 1}},
    "must_cite": ["FIN-POL-002", "FIN-POL-001#s3"],
    "must_not_cite": ["FIN-POL-003-OLD"],
    "unknowns": ["get_purchase_order"],
    "assumptions": ["a", "b"],
    "forbidden_actions": ["POST"],
    "extra": {"po_attempts": 3, "never_outcome": "REJECT_INVALID"},
}


def observation(**changes):
    fields = {
        "outcome": "HOLD_FOR_INFORMATION",
        "primary_category": "MISSING_RECEIPT",
        "owner": "AP Operations",
        "run_status_before_decision": "AWAITING_APPROVAL",
        "rows_before_decision": {"block": 1, "ledger": {}},
        "run_status_after_decision": "COMPLETE",
        "rows_after_decision": {"block": 1, "ledger": {"HOLD": 1}},
        "citations": frozenset({"FIN-POL-002#s4", "FIN-POL-001#s3"}),
        "unknowns": ("get_purchase_order",),
        "assumptions": ("a", "b"),
        "actions": ("BLOCK", "approval_request", "HOLD"),
        "extra": {"po_attempts": 3, "never_outcome": "HOLD_FOR_INFORMATION"},
    }
    return Observation(**{**fields, **changes})


def failed_keys(expected=EXPECTED, **changes):
    return {item.key for item in compare(expected, observation(**changes)) if not item.passed}


def test_a_matching_observation_passes_every_assertion():
    assert failed_keys() == set()


@pytest.mark.parametrize(
    ("change", "key"),
    [
        ({"outcome": "APPROVE_FOR_POSTING"}, "outcome"),
        ({"primary_category": None}, "primary_category"),
        ({"owner": "someone"}, "owner"),
        ({"run_status_before_decision": "FAILED"}, "run_status_before_decision"),
        ({"run_status_after_decision": "FAILED"}, "run_status_after_decision"),
        ({"rows_before_decision": {"block": 0, "ledger": {}}}, "rows_before_decision"),
        ({"rows_after_decision": {"block": 1, "ledger": {"HOLD": 2}}}, "rows_after_decision"),
        ({"citations": frozenset({"FIN-POL-001#s3"})}, "must_cite[FIN-POL-002]"),
        ({"unknowns": ()}, "unknowns"),
        ({"assumptions": ("a",)}, "assumptions"),
        ({"actions": ("POST",)}, "forbidden_actions[POST]"),
        ({"extra": {"po_attempts": 2, "never_outcome": "HOLD_FOR_INFORMATION"}}, "extra.po_attempts"),
        ({"extra": {"po_attempts": 3, "never_outcome": "REJECT_INVALID"}}, "extra.never_outcome"),
    ],
)
def test_each_mismatch_fails_its_own_assertion(change, key):
    assert failed_keys(**change) == {key}


def test_a_forbidden_citation_fails():
    keys = failed_keys(citations=frozenset({"FIN-POL-002#s4", "FIN-POL-001#s3", "FIN-POL-003-OLD#s2"}))
    assert keys == {"must_not_cite[FIN-POL-003-OLD]"}


def test_an_extra_with_no_check_fails_rather_than_passing():
    expected = {**EXPECTED, "extra": {"new_rule": NO_CHECK}}
    assert failed_keys(expected) == {"extra.new_rule"}


def test_an_unknown_top_level_expectation_fails():
    assert failed_keys({**EXPECTED, "new_key": 1}) == {"new_key"}


def test_every_signed_off_extra_has_a_check():
    from finagent.eval.scenarios import _EXTRA_CHECKS

    keys = {key for case in load_expectations().values() for key in case.get("extra", {})}
    assert keys <= set(_EXTRA_CHECKS)


@pytest.mark.parametrize(
    ("wanted", "found"),
    [
        ("FIN-POL-002", True),
        ("FIN-POL-002#s4", True),
        ("FIN-POL-002#s1", False),
        ("FIN-POL-00", False),
        ("CASE:goods_receipt_note", True),
        ("CASE:goods", False),
    ],
)
def test_citation_matching(wanted, found):
    assert is_cited(wanted, frozenset({"FIN-POL-002#s4", "CASE:goods_receipt_note"})) is found


def test_a_crashing_case_is_reported_as_failed():
    def crash(_case, _expected):
        raise RuntimeError("boom")

    result = run_case("FIN-001", EXPECTED, crash)
    assert not result.passed
    assert result.assertions[0].key == "execution"


def test_a_case_with_no_assertions_does_not_pass():
    assert not CaseResult("FIN-001", ()).passed


def test_run_scenarios_covers_every_given_case():
    results = run_scenarios({"A": EXPECTED, "B": EXPECTED}, lambda _case, _expected: observation())
    assert [(item.case_id, item.passed) for item in results] == [("A", True), ("B", True)]


def artifacts():
    return {
        "request": {"case_id": "FIN-001", "notes": "account 123456789"},
        "key_audit_events": [{"event": "approval_requested"}],
        "final_result": {"recommendation": {"outcome": "APPROVE_FOR_POSTING"}},
        "approval_request": {"action": "POST"},
    }


def payload(passed=True):
    assertion = Assertion("outcome", "A", "A" if passed else "B", passed)
    case = CaseResult("FIN-001", (assertion,), artifacts())
    return build_payload([case], {"rule_source_recall_at_5": {"hits": 27, "total": 31}}, NOW)


def test_payload_is_labelled_and_table_shows_each_assertion():
    data = payload(passed=False)
    table = render_table(data)
    assert data["label"] == OFFLINE_LABEL == "control flow, fake model"
    assert data["passed"] is False
    assert "| FIN-001 | outcome |" in table
    assert "FAIL" in table
    assert "Assertions passed: 0/1" in table


def test_samples_are_rendered_from_the_payload_and_masked(tmp_path):
    written = write_samples(payload(), tmp_path)
    text = written[0].read_text(encoding="utf-8")
    assert written[0].name == "FIN-001.md"
    assert "control flow, fake model" in text
    assert "123456789" not in text
    assert all(title in text for title in ("Request", "Key audit events", "Final result", "Approval request"))


def test_stored_runs_are_listed_with_per_case_results(tmp_path):
    store_payload(payload(passed=False), tmp_path, NOW)
    runs = list_runs(tmp_path)
    assert runs[0]["cases"] == {"FIN-001": False}
    assert runs[0]["passed"] is False


def test_an_unreadable_stored_run_is_listed_as_an_error(tmp_path):
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "bad.json").write_text("{", encoding="utf-8")
    assert list_runs(tmp_path)[0]["error"] == "unreadable"


def test_eval_list_command_prints_stored_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("FINAGENT_HOME", str(tmp_path))
    store_payload(payload(), tmp_path, NOW)
    result = CliRunner().invoke(app, ["eval", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["runs"][0]["cases"] == {"FIN-001": True}


def test_live_refuses_without_an_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FINAGENT_HOME", str(tmp_path))
    result = CliRunner().invoke(app, ["eval", "--live", "--configs", "template,haiku"])
    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.stderr


def prepared(_case_id):
    case = build_case("FIN-004")
    evaluation = RationaleInput.from_evaluation(evaluate(case, FixedClock(case.as_of)))
    chunk = PromptChunk("FIN-POL-002#s4", "FIN-POL-002", "AUTHORITATIVE", "Goods need a receipt.")
    return evaluation, (chunk,)


def live(tmp_path, llm, configs=("template", "haiku")):
    results = tmp_path / "EVAL_RESULTS.md"
    results.write_text("# Evaluation results\n\n## Retrieval\n\nkeep me\n", encoding="utf-8")
    data, path = run_live(configs, lambda: llm, tmp_path, NOW, prepared, results)
    return data, path, results


def test_live_runs_each_input_once_per_config_and_keeps_other_sections(tmp_path):
    data, path, results = live(tmp_path, FakeLLM("good"))
    assert [(row["case_id"], row["config"]) for row in data["runs"]] == [
        ("FIN-001", "template"),
        ("FIN-003", "template"),
        ("FIN-001", "haiku"),
        ("FIN-003", "haiku"),
    ]
    text = results.read_text(encoding="utf-8")
    assert "keep me" in text
    assert "## Model" in text
    assert "indicative" in text
    assert path.name.startswith("live-")


def test_live_template_config_makes_no_model_call(tmp_path):
    llm = FakeLLM("good")
    data, _, _ = live(tmp_path, llm, configs=("template",))
    assert llm.calls == 0
    assert all(row["model_calls"] == 0 and row["source"] == "TEMPLATE" for row in data["runs"])


def test_live_gate_is_computed_on_raw_output_before_validation(tmp_path):
    data, _, _ = live(tmp_path, FakeLLM("injection_obeying"), configs=("haiku",))
    row = data["runs"][0]
    assert row["raw_forbidden_claim"] is True
    assert row["source"] == "TEMPLATE"
    assert row["first_try_valid"] is False
    assert row["manual_read"] == ""


def test_live_counts_truncation_and_stores_raw_output(tmp_path):
    data, _, _ = live(tmp_path, FakeLLM("stop:max_tokens"), configs=("haiku",))
    assert data["runs"][0]["truncated_or_refused"] is True
    stored = list((tmp_path / "eval").glob("live-raw-*/*.txt"))
    assert stored


def test_live_rejects_an_unknown_config(tmp_path):
    with pytest.raises(ValueError):
        run_live(("sonnet",), FakeLLM, tmp_path, NOW, prepared, tmp_path / "r.md")


def test_model_section_rewrite_leaves_other_sections_unchanged(tmp_path):
    path = tmp_path / "r.md"
    path.write_text("# T\n\n## Retrieval\n\nA\n\n## Model\n\nold\n\n## Other\n\nB\n", encoding="utf-8")
    write_model_section(path, "## Model\n\nnew\n\n")
    text = path.read_text(encoding="utf-8")
    assert text == "# T\n\n## Retrieval\n\nA\n\n## Model\n\nnew\n\n## Other\n\nB\n"


def test_exception_coverage_accepts_rule_id_or_category_words():
    evaluation, _ = prepared("FIN-004")
    total = len(evaluation.exceptions)
    assert exception_coverage(evaluation, "RECEIPT-REQUIRED applies") == (1, total)
    assert exception_coverage(evaluation, "a missing receipt") == (total, total)
    assert exception_coverage(evaluation, "nothing relevant") == (0, total)
