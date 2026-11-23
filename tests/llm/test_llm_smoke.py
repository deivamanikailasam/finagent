"""One real model call. Run by the owner with `make test-llm`; never part of the offline suite."""

from pathlib import Path

import anthropic
import pytest

from finagent.eval.live import prepare_inputs
from finagent.llm.anthropic_adapter import AnthropicLLM, load_model_config
from finagent.llm.router import Router

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.llm
def test_one_real_rationale_call():
    config = load_model_config(ROOT / "config/model.yaml")
    evaluation, chunks = prepare_inputs("FIN-001")  # the same inputs a real run gives the model
    events = []
    router = Router(AnthropicLLM(config), "llm", config, lambda o, **kw: events.append((o, kw)))
    try:
        result = router.rationale(evaluation, chunks)
    except anthropic.NotFoundError:
        pytest.skip("the configured model was not found")
    if events and events[0][1].get("error") == "NotFoundError":
        pytest.skip("the configured model was not found")
    assert events, "no model call was audited"
    assert events[-1][0] == "ok"
    assert result.source == "LLM"
    assert events[-1][1]["input_tokens"] > 0
