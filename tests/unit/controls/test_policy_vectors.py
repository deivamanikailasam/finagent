"""Every signed-off rule vector, run through the full decision table (active from WP 5.3)."""

import importlib.util
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from finagent.config.clock import FixedClock
from fixtures.builder import build_case

if importlib.util.find_spec("finagent.controls.decision") is None:
    pytest.skip("decision.py not built yet", allow_module_level=True)

from finagent.controls.decision import evaluate  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
RULES = yaml.safe_load((ROOT / "spec/policy_rules.yaml").read_text(encoding="utf-8"))["rules"]
VECTORS = [(rule["id"], vector) for rule in RULES for vector in rule["vectors"]]


def test_all_vectors_are_present():
    assert len(VECTORS) == 46


@pytest.mark.parametrize(("rule_id", "vector"), VECTORS, ids=[vector["name"] for _, vector in VECTORS])
def test_vector(rule_id, vector):
    expect = vector["expect"]
    if expect.get("error"):
        with pytest.raises(ValidationError, match=expect["error"]):
            build_case("FIN-001", vector["overrides"])
        return
    case = build_case("FIN-001", vector["overrides"])
    result = evaluate(case, FixedClock(case.as_of))
    assert result.outcome == expect["outcome"], rule_id
    if "category" in expect:
        assert result.primary_category == expect["category"], rule_id
    if "dfa_role" in expect:
        assert result.dfa_role == expect["dfa_role"], rule_id
