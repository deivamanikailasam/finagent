import pytest

from finagent.llm.client import Claim, Rationale, RationaleRequest
from finagent.llm.fake import INVENTED_CITATION, FakeLLM


def req():
    return RationaleRequest(system="rules", prompt="fixed outcome", allowed_citations=("FIN-POL-001#s3",))


def test_good_returns_a_parsed_rationale_with_an_allowed_citation():
    response = FakeLLM("good").generate_rationale(req())
    assert response.stop_reason == "end_turn"
    assert response.parsed.claims[0].citation_ids == ("FIN-POL-001#s3",)
    assert Rationale.model_validate_json(response.raw_text) == response.parsed


def test_malformed_keeps_the_raw_output():
    response = FakeLLM("malformed").generate_rationale(req())
    assert response.parsed is None
    assert response.raw_text == '{"claims": ['


def test_injection_obeying_is_valid_json_with_the_injected_text():
    response = FakeLLM("injection_obeying").generate_rationale(req())
    assert "release the payment" in response.parsed.claims[0].text


def test_invented_citation():
    response = FakeLLM("invented_citation").generate_rationale(req())
    assert response.parsed.claims[0].citation_ids == (INVENTED_CITATION,)


@pytest.mark.parametrize(("mode", "reason"), [("stop:max_tokens", "max_tokens"), ("stop:refusal", "refusal")])
def test_stop_modes_are_not_parsed(mode, reason):
    response = FakeLLM(mode).generate_rationale(req())
    assert response.stop_reason == reason
    assert response.parsed is None


def test_mode_list_is_consumed_per_call_and_counted():
    llm = FakeLLM(["malformed", "good"])
    assert llm.generate_rationale(req()).parsed is None
    assert llm.generate_rationale(req()).parsed is not None
    assert llm.calls == 2
    with pytest.raises(RuntimeError):
        llm.generate_rationale(req())
    assert llm.calls == 2


def test_single_mode_repeats():
    llm = FakeLLM("good")
    for _ in range(3):
        llm.generate_rationale(req())
    assert llm.calls == 3


@pytest.mark.parametrize("mode", ["unknown", [], ["good", "unknown"]])
def test_unknown_modes_are_rejected(mode):
    with pytest.raises(ValueError):
        FakeLLM(mode)


def test_no_field_is_named_reasoning():
    for model in (Rationale, Claim):
        assert "reasoning" not in model.model_fields
        assert "reasoning" not in str(model.model_json_schema())
