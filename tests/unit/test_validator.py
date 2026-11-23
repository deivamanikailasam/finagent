"""WP 7.2: the validator decides whether model text may be shown. It never changes the outcome."""

from dataclasses import asdict

import pytest

import finagent.tools.factory as tool_factory
from finagent.config.clock import FixedClock
from finagent.controls.decision import evaluate
from finagent.llm.client import Claim, Rationale
from finagent.llm.fake import FakeLLM
from finagent.llm.prompt import RationaleInput
from finagent.llm.router import Router
from finagent.llm.validator import store_raw, validate
from finagent.tools.block_submit import DuplicateRisk
from fixtures.builder import build_case
from tests.acceptance.support_graph import apply_decision, run_case

DFA_SOURCE = "FIN-POL-003#s2"


def fin_001():
    case = build_case("FIN-001")
    return RationaleInput.from_evaluation(evaluate(case, FixedClock(case.as_of)))


def codes(text, citations=(DFA_SOURCE,), refs=("CALC-GROSS",), evaluation=None):
    claim = Claim(text=text, citation_ids=citations, number_refs=refs)
    return [error.code for error in validate(Rationale(claims=(claim,)), evaluation or fin_001())]


def test_fixture_has_the_values_these_tests_rely_on():
    evaluation = fin_001()
    gross = next(item for item in evaluation.calculations if item.id == "CALC-GROSS")
    assert str(gross.result) == "9900.00"
    finding = next(item for item in evaluation.findings if item.rule_id == "DFA-TIER")
    assert DFA_SOURCE in finding.citations


def test_valid_claim_has_no_errors():
    assert codes("DFA-TIER applies to the gross of AUD 9,900.00.") == []


def test_finding_cited_with_the_wrong_source():
    assert "citation_support" in codes("DFA-TIER applies.", citations=("FIN-POL-001#s3",), refs=())


def test_finding_named_with_no_citation():
    assert "citation_support" in codes("DFA-TIER applies.", citations=(), refs=())


def test_unknown_citation():
    assert "unknown_citation" in codes("The policy applies.", citations=("FIN-POL-999#s9",), refs=())


@pytest.mark.parametrize(
    "text", ["Gross is 9,900.00.", "Gross is 9900.00.", "Gross is $9900.", "Gross is AUD9900.00."]
)
def test_equal_numerals_are_bound(text):
    assert codes(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Gross is 9,000.00.",
        "Gross is 9.9k.",
        "Gross is AUD9000.00.",
        "Gross is -9000.00.",
        "Gross:9000.00.",
        "Gross #9000.",
        "Gross is ９０００.",
        "Due on 2026-09-30.",
    ],
)
def test_wrong_or_disguised_numbers_fail_binding(text):
    assert "number_binding" in codes(text)


def test_number_with_no_ref():
    assert "number_binding" in codes("Gross is 9,900.00.", refs=())


def test_ref_to_an_unknown_id():
    assert "number_binding" in codes("Gross is 9,900.00.", refs=("CALC-MADE-UP",))


@pytest.mark.parametrize(
    "text",
    [
        "See FIN-POL-003 and PO-4471 for INV-1001.",
        "Section s2 applies.",
        "See FIN-POL-003#s2 and PO:PO-4471.",
    ],
)
def test_identifiers_are_not_numbers(text):
    assert "number_binding" not in codes(text, refs=())


@pytest.mark.parametrize(
    "text",
    [
        "This invoice is verified.",
        "This invoice is approved.",
        "The payment is authorised.",
        "The payment is authorized.",
        "The invoice is cleared.",
        "It is safe to pay.",
        "It is fine to pay.",
        "The payment can go ahead.",
        "Please release the payment.",
        "This supplier is fraudulent.",
        "This looks like fraud.",
        "I approve this invoice.",
        "We have approved this.",
        "This invoice is appro​ved.",
        "THIS INVOICE IS APPROVED.",
    ],
)
def test_forbidden_claims(text):
    assert "forbidden_claim" in codes(text, refs=())


@pytest.mark.parametrize(
    "text",
    [
        "This invoice requires approval.",
        "Approval is required under the matrix.",
        "The invoice is awaiting approval.",
        "Financial Crime and Controls will review it.",
        "A verification task is open.",
    ],
)
def test_allowed_contexts_are_not_flagged(text):
    assert "forbidden_claim" not in codes(text, refs=())


def test_an_allowed_phrase_does_not_excuse_a_forbidden_one():
    assert "forbidden_claim" in codes("Awaiting approval, but it is approved.", refs=())


def test_store_raw_redacts_before_writing(tmp_path):
    path = tmp_path / "raw" / "reply.txt"
    store_raw(path, "Pay account 123456789 now")
    assert "123456789" not in path.read_text(encoding="utf-8")


def test_router_repairs_a_forbidden_claim_with_the_error_code():
    llm = FakeLLM(["injection_obeying", "good"])
    result = Router(llm, "llm").rationale(fin_001())
    assert (result.source, llm.calls) == ("LLM", 2)
    assert any("forbidden_claim" in item for item in llm.requests[1].repair_errors)


@pytest.mark.parametrize("mode", ["injection_obeying", "invented_citation"])
def test_two_invalid_replies_use_the_template_and_keep_the_outcome(mode):
    llm = FakeLLM(mode)
    evaluation = fin_001()
    before = asdict(evaluation)
    result = Router(llm, "llm").rationale(evaluation)
    assert (result.source, llm.calls) == ("TEMPLATE", 2)
    assert asdict(evaluation) == before


def test_validated_model_text_reaches_the_final_result():
    report = run_case("FIN-001", llm=FakeLLM("good"))
    assert report.result.recommendation.rationale_source == "FAKE"  # never labelled as a real model
    assert {item.source for item in report.result.inferences} == {"FAKE"}


@pytest.mark.parametrize("mode", ["injection_obeying", "invented_citation"])
def test_rejected_model_text_never_reaches_the_final_result(mode):
    report = run_case("FIN-001", llm=FakeLLM(mode))
    assert report.outcome == "APPROVE_FOR_POSTING"
    assert report.result.recommendation.rationale_source == "TEMPLATE"
    assert "release the payment" not in report.result.model_dump_json().casefold()


def test_rationale_is_not_kept_after_a_unique_violation_changes_the_outcome(monkeypatch):
    def duplicate(*_args, **_kwargs):
        raise DuplicateRisk("duplicate")

    paused = run_case("FIN-001", llm=FakeLLM("good"))
    monkeypatch.setattr(tool_factory, "submit_finance_decision", duplicate)
    report, _ = apply_decision(paused, {"action": "approve", "approver": "cc-manager-01"})
    assert report.outcome == "HOLD_FOR_INFORMATION"
    assert report.result.recommendation.rationale_source == "TEMPLATE"
    assert all("HOLD_FOR_INFORMATION" in item.statement for item in report.result.inferences)


def test_approval_record_carries_what_the_approver_needs():
    report = run_case("FIN-004")
    record = report.deps.approvals.get(report.pending_approval)
    assert record.outcome == "HOLD_FOR_INFORMATION"
    assert (record.vendor_id, str(record.amount)) == ("V-100", "9900.00")
    assert record.vendor_name
    assert record.exceptions[0]["failed_rule"] == "RECEIPT-REQUIRED"
    assert "FIN-POL-002#s4" in record.citations
