"""WP 7.1: primary model, one repair, then the template. The outcome never moves."""

from dataclasses import asdict

import pytest

from finagent.config.clock import FixedClock
from finagent.controls.decision import evaluate
from finagent.llm.fake import FakeLLM
from finagent.llm.prompt import RationaleInput
from finagent.llm.router import Router
from fixtures.builder import build_case
from tests.acceptance.support_graph import run_case


def evaluation(case_id="FIN-004"):
    case = build_case(case_id)
    return RationaleInput.from_evaluation(evaluate(case, FixedClock(case.as_of)))


def test_good_reply_needs_one_call():
    llm = FakeLLM("good")
    result = Router(llm, "llm").rationale(evaluation())
    assert (result.source, llm.calls) == ("LLM", 1)


def test_malformed_then_good_needs_two_calls_and_passes_the_error():
    llm = FakeLLM(["malformed", "good"])
    result = Router(llm, "llm").rationale(evaluation())
    assert (result.source, llm.calls) == ("LLM", 2)
    assert llm.requests[0].repair_errors == ()
    assert llm.requests[1].repair_errors == ("malformed_json",)


def test_two_malformed_replies_use_the_template_and_keep_the_outcome():
    llm = FakeLLM(["malformed", "malformed"])
    item = evaluation()
    before = asdict(item)
    result = Router(llm, "llm").rationale(item)
    assert (result.source, llm.calls) == ("TEMPLATE", 2)
    assert asdict(item) == before


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_stop_reasons_are_malformed_and_counted(stop):
    llm = FakeLLM([f"stop:{stop}", f"stop:{stop}"])
    result = Router(llm, "llm").rationale(evaluation())
    assert result.source == "TEMPLATE"
    assert result.stop_reasons == {stop: 2}
    assert llm.requests[1].repair_errors == (f"stop:{stop}",)


def test_template_source_makes_no_model_call():
    llm = FakeLLM("good")
    result = Router(llm, "template").rationale(evaluation())
    assert (result.source, llm.calls, result.model) == ("TEMPLATE", 0, None)


def test_provider_failure_goes_straight_to_the_template():
    class Broken:
        calls = 0

        def generate_rationale(self, _request):
            Broken.calls += 1
            raise ConnectionError("down")

    events = []
    result = Router(Broken(), "llm", audit=lambda outcome, **kw: events.append((outcome, kw))).rationale(
        evaluation()
    )
    assert (result.source, Broken.calls) == ("TEMPLATE", 1)
    assert events[0][0] == "error"


def test_template_mentions_every_exception_and_unknown():
    item = evaluation()
    text = " ".join(
        claim.text for claim in Router(FakeLLM("good"), "template").rationale(item).rationale.claims
    )
    assert all(exception.failed_rule in text for exception in item.exceptions)
    assert all(unknown.source in text for unknown in item.unknowns)


def test_each_hop_is_audited_with_the_required_fields():
    events = []
    Router(FakeLLM(["malformed", "good"]), "llm", audit=lambda o, **kw: events.append((o, kw))).rationale(
        evaluation()
    )
    assert [outcome for outcome, _ in events] == ["malformed_json", "ok"]
    required = {"provider", "model", "response_id", "region", "prompt_hash", "input_tokens", "output_tokens"}
    assert all(required <= set(detail) for _, detail in events)
    assert all(detail["cost_usd"] == "unpriced" for _, detail in events)


@pytest.mark.parametrize("mode", ["injection_obeying", "invented_citation", "malformed"])
def test_unusable_model_text_never_reaches_the_final_result(mode):
    report = run_case("FIN-001", llm=FakeLLM(mode))
    assert report.outcome == "APPROVE_FOR_POSTING"
    assert report.result.recommendation.rationale_source == "TEMPLATE"
    assert "release the payment" not in report.result.model_dump_json()


def test_a_failing_model_does_not_fail_the_run():
    class Broken:
        def generate_rationale(self, _request):
            raise RuntimeError("down")

    report = run_case("FIN-004", llm=Broken())
    assert (report.status, report.outcome) == ("AWAITING_APPROVAL", "HOLD_FOR_INFORMATION")
