"""One real model call. Run by the owner with `make test-llm`; never part of the offline suite."""

from pathlib import Path

import anthropic
import pytest

from finagent.eval.live import prepare_inputs
from finagent.llm.anthropic_adapter import AnthropicLLM, load_model_config
from finagent.llm.router import Router
from finagent.llm.validator import store_raw

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "var" / "llm_smoke"  # redacted replies, kept so a rejection can be diagnosed


@pytest.mark.llm
def test_one_real_rationale_call():
    config = load_model_config(ROOT / "config/model.yaml")
    evaluation, chunks = prepare_inputs("FIN-001")  # the same inputs a real run gives the model
    events = []

    def keep_raw(hop, raw_text):
        store_raw(RAW_DIR / f"reply-{hop}.txt", raw_text)

    router = Router(AnthropicLLM(config), "llm", config, lambda o, **kw: events.append((o, kw)), keep_raw)
    try:
        result = router.rationale(evaluation, chunks)
    except anthropic.NotFoundError:
        pytest.skip("the configured model was not found")
    if events and events[0][1].get("error") == "NotFoundError":
        pytest.skip("the configured model was not found")
    assert events, "no model call was audited"
    outcomes = [outcome for outcome, _ in events]
    assert events[-1][0] == "ok", f"hop outcomes: {outcomes}; redacted replies in {RAW_DIR}"
    assert result.source == "LLM"
    assert events[-1][1]["input_tokens"] > 0
