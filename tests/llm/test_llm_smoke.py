"""One real model call. Run by the owner with `make test-llm`; never part of the offline suite."""

from pathlib import Path

import anthropic
import pytest

from finagent.config.clock import FixedClock
from finagent.controls.decision import evaluate
from finagent.llm.anthropic_adapter import AnthropicLLM, load_model_config
from finagent.llm.prompt import RationaleInput
from finagent.llm.router import Router
from fixtures.builder import build_case

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.llm
def test_one_real_rationale_call():
    config = load_model_config(ROOT / "config/model.yaml")
    case = build_case("FIN-001")
    decided = evaluate(case, FixedClock(case.as_of))
    events = []
    router = Router(AnthropicLLM(config), "llm", config, lambda o, **kw: events.append((o, kw)))
    try:
        result = router.rationale(RationaleInput.from_evaluation(decided))
    except anthropic.NotFoundError:
        pytest.skip("the configured model was not found")
    if events and events[0][1].get("error") == "NotFoundError":
        pytest.skip("the configured model was not found")
    assert events, "no model call was audited"
    assert events[-1][0] == "ok"
    assert result.source == "LLM"
    assert events[-1][1]["input_tokens"] > 0
