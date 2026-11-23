"""WP 5.1: the signed-off vectors for the calculator and matching rules, run directly."""

from pathlib import Path

import pytest
import yaml

from finagent.config.clock import FixedClock
from finagent.controls.calculator import calculate
from finagent.controls.matching import MATCHING_RULES, check
from fixtures.builder import build_case

ROOT = Path(__file__).resolve().parents[2]
RULES = yaml.safe_load((ROOT / "spec/policy_rules.yaml").read_text(encoding="utf-8"))["rules"]
ALL_VECTORS = [
    (rule["id"], vector) for rule in RULES for vector in rule["vectors"] if "outcome" in vector["expect"]
]
MODULE_VECTORS = [(rule_id, vector) for rule_id, vector in ALL_VECTORS if rule_id in MATCHING_RULES]
APPROVE_VECTORS = [item for item in ALL_VECTORS if item[1]["expect"]["outcome"] == "APPROVE_FOR_POSTING"]


def failed(vector):
    case = build_case("FIN-001", vector["overrides"])
    findings = check(case, calculate(case), FixedClock(case.as_of))
    return {item.rule_id: item for item in findings if item.outcome not in {"PASS", "UNKNOWN"}}


def test_module_covers_its_rules():
    assert len(MATCHING_RULES) == 16
    assert {rule_id for rule_id, _ in MODULE_VECTORS} == MATCHING_RULES


@pytest.mark.parametrize(
    ("rule_id", "vector"), MODULE_VECTORS, ids=[vector["name"] for _, vector in MODULE_VECTORS]
)
def test_rule_vector(rule_id, vector):
    expect = vector["expect"]
    results = failed(vector)
    if expect["outcome"] == "APPROVE_FOR_POSTING":
        assert rule_id not in results
    else:
        assert results[rule_id].outcome == expect["outcome"]
        assert results[rule_id].category == expect["category"]


@pytest.mark.parametrize(("rule_id", "vector"), APPROVE_VECTORS, ids=[v["name"] for _, v in APPROVE_VECTORS])
def test_an_approve_vector_has_no_matching_failure(rule_id, vector):
    assert failed(vector) == {}, rule_id
