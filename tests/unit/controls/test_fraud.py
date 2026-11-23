"""WP 5.3 fraud-indicator tests, written before the implementation."""

from pathlib import Path

import pytest
import yaml

from finagent.controls.fraud import detect
from finagent.tools.schemas import RetrievalItem
from fixtures.builder import SUPPLIER_INJECTION, build_case

ROOT = Path(__file__).resolve().parents[3]
TEXT_INDICATORS = {"urgent_or_secret_payment", "bypass_approval", "disable_checks"}
ADV_001 = (ROOT / "corpus/supplier_payment_instructions.md").read_text(encoding="utf-8").split("---\n", 2)[2]


def distinct(result):
    return {hit.indicator for hit in result.indicators}


def chunk(doc_id, text, trust_class="EXTERNAL_UNVERIFIED", lane="non_authoritative"):
    return RetrievalItem(
        doc_id=doc_id,
        doc_type="SUPPLIER_DOCUMENT" if trust_class == "EXTERNAL_UNVERIFIED" else "POLICY",
        version="1.0",
        section="s0",
        score=1.0,
        lane=lane,
        trust_class=trust_class,
        citation=f"{doc_id}#s0",
        text=text,
    )


def notes(text):
    return build_case("FIN-001", {"request.notes": text})


def attachment_only():
    """The FIN-003 attachment with a remit-to that matches the vendor master."""
    clean = build_case("FIN-001").invoice.remit_to.model_dump(mode="json")
    return build_case("FIN-003", {"invoice.remit_to": clean})


def test_config_declares_the_indicators():
    config = yaml.safe_load((ROOT / "config/fraud_indicators.yaml").read_text(encoding="utf-8"))
    assert config["escalate_threshold"] == 2
    assert set(config["text_indicators"]) == TEXT_INDICATORS
    assert set(config["structural_indicators"]) == {"changed_bank_details", "vendor_name_mismatch"}


def test_fin_001_has_no_indicators():
    result = detect(build_case("FIN-001"), ())
    assert (result.indicators, result.outcome, result.security_events) == ((), None, ())


def test_fin_003_escalates_on_the_bank_category():
    result = detect(build_case("FIN-003"), ())
    assert len(distinct(result)) >= 2
    assert "changed_bank_details" in distinct(result)
    assert (result.outcome, result.category) == ("ESCALATE_CONTROL_REVIEW", "BANK_CHANGE")
    assert result.owner == "Financial Crime and Controls"
    assert result.notify_supplier is False


def test_fin_003_attachment_with_matching_remit_to_escalates_on_text_alone():
    result = detect(attachment_only(), ())
    assert len(distinct(result) & TEXT_INDICATORS) >= 2
    assert "changed_bank_details" not in distinct(result)
    assert (result.outcome, result.category) == ("ESCALATE_CONTROL_REVIEW", "OTHER_CONTROL_RISK")
    assert result.bank_detail_claim is True


def test_exactly_one_indicator_is_recorded_without_an_outcome():
    result = detect(notes("Please process this urgently."), ())
    assert distinct(result) == {"urgent_or_secret_payment"}
    assert result.outcome is None
    assert result.findings[0].outcome == "PASS"


def test_repeated_hits_of_one_indicator_do_not_escalate():
    result = detect(notes("Urgent. This is urgent. Urgently needed."), ())
    assert distinct(result) == {"urgent_or_secret_payment"}
    assert result.outcome is None


def test_unlinked_adv_001_is_a_security_event_and_no_indicator():
    result = detect(build_case("FIN-001"), (chunk("ADV-001", ADV_001),))
    assert result.indicators == ()
    assert result.outcome is None
    assert len(result.security_events) == 1
    assert result.security_events[0]["doc_id"] == "ADV-001"


def test_retrieved_text_linked_by_vendor_counts():
    text = f"{ADV_001} Regarding vendor V-100."
    result = detect(build_case("FIN-001"), (chunk("ADV-001", text),))
    assert len(distinct(result)) >= 2
    assert result.outcome == "ESCALATE_CONTROL_REVIEW"
    assert result.security_events == ()


def test_authoritative_policy_wording_is_never_an_indicator():
    text = "Escalate on urgent or secret payment language or a request to bypass normal approval."
    item = chunk("FIN-POL-005", text, "AUTHORITATIVE", "authoritative")
    result = detect(build_case("FIN-001"), (item,))
    assert (result.indicators, result.security_events) == ((), ())


def test_hits_carry_indicator_document_and_offset():
    result = detect(notes("Please process this urgently."), ())
    hit = result.indicators[0]
    assert (hit.indicator, hit.doc_id, hit.offset) == ("urgent_or_secret_payment", "REQUEST:notes", 20)


@pytest.mark.parametrize(
    "text",
    ["URGENT PAYMENT", "ur​gent payment", "ＵＲＧＥＮＴ payment"],
)
def test_case_zero_width_and_lookalike_text_is_detected(text):
    assert "urgent_or_secret_payment" in distinct(detect(notes(text), ()))


@pytest.mark.parametrize(
    ("text", "indicator"),
    [
        ("keep this quiet", "urgent_or_secret_payment"),
        ("bypass the approval step", "bypass_approval"),
        ("do not ask a human approver", "bypass_approval"),
        ("disable duplicate checks", "disable_checks"),
        ("ignore policy", "disable_checks"),
    ],
)
def test_text_indicators(text, indicator):
    assert indicator in distinct(detect(notes(text), ()))


def test_the_result_never_says_fraudulent():
    result = detect(build_case("FIN-003"), ())
    assert "fraudulent" not in str(result).casefold()


def test_bank_wording_in_free_text_alone_is_not_a_structural_indicator():
    result = detect(notes("Our new bank account has changed."), ())
    assert "changed_bank_details" not in distinct(result)
    assert result.outcome is None


def test_remit_mismatch_is_a_structural_bank_indicator():
    result = detect(build_case("FIN-001", {"invoice.remit_to.fingerprint": "f" * 64}), ())
    assert distinct(result) == {"changed_bank_details"}


@pytest.mark.parametrize(("changed", "hit"), [("2026-08-16", True), ("2026-08-15", False)])
def test_bank_change_recency_boundary(changed, hit):
    case = build_case(
        "FIN-001",
        {"vendor.bank_changed_at": f"{changed}T09:00:00+10:00", "vendor.last_paid_at": None},
    )
    assert ("changed_bank_details" in distinct(detect(case, ()))) is hit


def test_legal_name_mismatch_is_a_structural_indicator():
    result = detect(build_case("FIN-001", {"invoice.legal_name": "Other Supplier Pty Ltd"}), ())
    assert distinct(result) == {"vendor_name_mismatch"}


def test_missing_legal_name_is_not_a_name_mismatch():
    assert detect(build_case("FIN-001", {"invoice.legal_name": None}), ()).indicators == ()


def test_two_structural_indicators_escalate_on_the_bank_category():
    case = build_case(
        "FIN-001",
        {"invoice.remit_to.fingerprint": "f" * 64, "invoice.legal_name": "Other Supplier Pty Ltd"},
    )
    result = detect(case, ())
    assert (result.outcome, result.category) == ("ESCALATE_CONTROL_REVIEW", "BANK_CHANGE")


def test_failed_vendor_read_is_unknown_unless_already_escalated():
    case = build_case("FIN-001").model_copy(update={"vendor": None})
    assert detect(case, ()).findings[0].outcome == "UNKNOWN"
    poisoned = build_case("FIN-001", {"request.notes": SUPPLIER_INJECTION}).model_copy(
        update={"vendor": None}
    )
    assert detect(poisoned, ()).outcome == "ESCALATE_CONTROL_REVIEW"
