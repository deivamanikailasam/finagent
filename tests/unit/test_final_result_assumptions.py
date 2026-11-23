"""FinalResult must state how an owner was assumed, and never point at an unknown person."""

from pathlib import Path

import pytest
import yaml

from finagent.config.clock import FixedClock
from finagent.controls.decision import NEXT_ACTION, UNKNOWN_RECEIPTER_ACTION, evaluate
from finagent.domain.enums import ExceptionCategory
from fixtures.builder import build_case
from tests.acceptance.support_graph import run_case

ROOT = Path(__file__).resolve().parents[2]
EXPECTATIONS = yaml.safe_load((ROOT / "tests/scenarios/expectations.yaml").read_text(encoding="utf-8"))[
    "cases"
]


def decide(case_id, overrides=None):
    case = build_case(case_id, overrides)
    return evaluate(case, FixedClock(case.as_of))


@pytest.mark.parametrize("case_id", sorted(EXPECTATIONS))
def test_final_result_reports_exactly_the_expected_assumptions(case_id):
    report = run_case(case_id)
    assert [item.statement for item in report.result.assumptions] == EXPECTATIONS[case_id]["assumptions"]


@pytest.mark.parametrize("case_id", sorted(EXPECTATIONS))
def test_final_result_cites_the_policy_source_of_its_outcome(case_id):
    paused = run_case(case_id)
    assert paused.result.recommendation.citations == ("FIN-POL-001#s3",)
    block = EXPECTATIONS[case_id]["decision"]
    decision = {"action": block["action"], "approver": block["approver"], "reason": block.get("reason")}
    finished = run_case(case_id, decision=decision)
    assert finished.result.recommendation.citations == ("FIN-POL-001#s3",)


@pytest.mark.parametrize("case_id", sorted(EXPECTATIONS))
def test_assumptions_survive_the_human_decision(case_id):
    block = EXPECTATIONS[case_id]["decision"]
    decision = {"action": block["action"], "approver": block["approver"], "reason": block.get("reason")}
    report = run_case(case_id, decision=decision)
    assert [item.statement for item in report.result.assumptions] == EXPECTATIONS[case_id]["assumptions"]


def test_fin_004_next_action_does_not_name_an_unknown_receipter():
    report = run_case("FIN-004")
    action = report.result.recommendation.next_action
    assert action == UNKNOWN_RECEIPTER_ACTION
    assert "designated" not in action
    assert report.result.recommendation.owner == "AP Operations"


def test_missing_receipt_with_a_po_keeps_the_table_action():
    result = decide("FIN-001", {"po.receipts": []})
    assert result.primary_category == ExceptionCategory.MISSING_RECEIPT
    assert result.next_action == NEXT_ACTION[ExceptionCategory.MISSING_RECEIPT]


def test_fraud_owner_is_never_reported_as_an_assumption():
    result = decide("FIN-003")
    assert result.owner == "Financial Crime and Controls"
    assert result.assumptions == ()


def test_assumption_ids_are_unique():
    statements = decide("FIN-001").assumptions
    assert len({item.id for item in statements}) == len(statements)
