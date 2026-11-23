"""WP 5.2: the signed-off vectors for the duplicate, vendor and authority rules, run directly."""

from pathlib import Path

import pytest
import yaml

from finagent.config.clock import FixedClock
from finagent.controls import duplicates, vendor_authority
from fixtures.builder import build_case

ROOT = Path(__file__).resolve().parents[2]
RULES = yaml.safe_load((ROOT / "spec/policy_rules.yaml").read_text(encoding="utf-8"))["rules"]
MODULE_RULES = duplicates.DUPLICATE_RULES | vendor_authority.VENDOR_AUTHORITY_RULES
ALL_VECTORS = [
    (rule["id"], vector) for rule in RULES for vector in rule["vectors"] if "outcome" in vector["expect"]
]
MODULE_VECTORS = [(rule_id, vector) for rule_id, vector in ALL_VECTORS if rule_id in MODULE_RULES]
APPROVE_VECTORS = [item for item in ALL_VECTORS if item[1]["expect"]["outcome"] == "APPROVE_FOR_POSTING"]


def evaluate(vector):
    case = build_case("FIN-001", vector["overrides"])
    clock = FixedClock(case.as_of)
    result = vendor_authority.check(case, clock)
    findings = [*duplicates.check(case, clock), *result.findings]
    failed = {item.rule_id: item for item in findings if item.outcome not in {"PASS", "UNKNOWN"}}
    return failed, result.dfa_role


def test_modules_cover_their_rules():
    assert len(MODULE_RULES) == 13
    assert {rule_id for rule_id, _ in MODULE_VECTORS} == MODULE_RULES


@pytest.mark.parametrize(
    ("rule_id", "vector"), MODULE_VECTORS, ids=[vector["name"] for _, vector in MODULE_VECTORS]
)
def test_rule_vector(rule_id, vector):
    expect = vector["expect"]
    failed, role = evaluate(vector)
    if expect["outcome"] == "APPROVE_FOR_POSTING":
        assert rule_id not in failed
    else:
        assert failed[rule_id].outcome == expect["outcome"]
        assert failed[rule_id].category == expect["category"]
    if "dfa_role" in expect:
        assert role == expect["dfa_role"]


@pytest.mark.parametrize(("rule_id", "vector"), APPROVE_VECTORS, ids=[v["name"] for _, v in APPROVE_VECTORS])
def test_an_approve_vector_has_no_failure_in_these_modules(rule_id, vector):
    assert evaluate(vector)[0] == {}, rule_id
