import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from finagent.domain.case import Case
from finagent.domain.enums import ExceptionCategory, Outcome, ReasonCode, VendorStatus
from finagent.domain.requests import ProcessingRequest
from finagent.domain.results import ExceptionRecord, FinalResult, Recommendation, next_review_date

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
SHA = "a" * 64


def corpus_section(file_prefix: str, number: int) -> str:
    text = next(CORPUS.glob(f"{file_prefix}_*.md")).read_text(encoding="utf-8")
    return re.search(rf"^## {number}\..*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL).group(0)


def request(**overrides):
    fields = {
        "case_id": "FIN-001",
        "invoice_ref": "INV-1001",
        "vendor_id": "V-100",
        "amount": "9900.00",
        "currency": "AUD",
    }
    return ProcessingRequest(**{**fields, **overrides})


@pytest.mark.parametrize(
    ("enum", "file_prefix", "section", "extra"),
    [
        (Outcome, "01", 3, set()),
        (ExceptionCategory, "07", 1, set()),
        (VendorStatus, "04", 4, set()),
    ],
)
def test_enum_strings_are_verbatim_in_the_corpus(enum, file_prefix, section, extra):
    quoted = set(re.findall(r"`([A-Z_]+)`", corpus_section(file_prefix, section)))
    assert {member.value for member in enum} - extra <= quoted
    assert quoted <= {member.value for member in enum}


def test_enum_sizes():
    assert (len(Outcome), len(ExceptionCategory), len(VendorStatus)) == (5, 10, 5)


def test_request_round_trip_keeps_exact_decimal():
    original = request(notes="note", attachments=[{"ref": "A1", "sha256": SHA, "origin": "supplier"}])
    restored = ProcessingRequest.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.amount == Decimal("9900.00")
    assert str(restored.amount) == "9900.00"


@pytest.mark.parametrize("case_id", ["FIN-003b", "FIN-901"])
def test_valid_case_ids(case_id):
    assert request(case_id=case_id).case_id == case_id


@pytest.mark.parametrize("case_id", ["fin-001", "FIN-01", "FIN-001bb", "FIN-٠٠١", "FIN-001\n", "../FIN-001"])
def test_invalid_case_ids(case_id):
    with pytest.raises(ValidationError):
        request(case_id=case_id)


@pytest.mark.parametrize("amount", ["0", "0.00", "-9900.00"])
def test_non_positive_amount_is_unsupported_document_type(amount):
    with pytest.raises(ValidationError, match=ReasonCode.UNSUPPORTED_DOCUMENT_TYPE.value):
        request(amount=amount)


@pytest.mark.parametrize("amount", [9900.0, True, "9900.001", "NaN", "Infinity", "1234567890123456"])
def test_inexact_or_oversized_amount_is_rejected(amount):
    with pytest.raises(ValidationError):
        request(amount=amount)


def test_fifteen_digit_amount_is_accepted():
    assert request(amount="1234567890123.45").amount == Decimal("1234567890123.45")


def test_notes_bound():
    assert len(request(notes="x" * 4000).notes) == 4000
    with pytest.raises(ValidationError):
        request(notes="x" * 4001)


def test_attachment_bound():
    attachment = {"ref": "A", "sha256": SHA, "origin": "internal"}
    assert len(request(attachments=[attachment] * 10).attachments) == 10
    with pytest.raises(ValidationError):
        request(attachments=[attachment] * 11)


@pytest.mark.parametrize("currency", ["AUD", "USD", "NZD", "EUR", "GBP"])
def test_currency_allow_list(currency):
    assert request(currency=currency).currency == currency


def test_other_currency_is_rejected():
    with pytest.raises(ValidationError):
        request(currency="JPY")


def test_request_is_frozen_and_closed():
    with pytest.raises(ValidationError):
        request().amount = Decimal("1.00")
    with pytest.raises(ValidationError):
        request(unexpected="x")


def exception_record(**overrides):
    fields = {
        "category": "PRICE_VARIANCE",
        "failed_rule": "TOL-GOODS",
        "citations": ["FIN-POL-002#s2"],
        "owner": "requester",
        "next_review_date": date(2026, 9, 18),
    }
    return ExceptionRecord(**{**fields, **overrides})


def test_exception_record_requires_a_citation():
    assert exception_record().citations == ("FIN-POL-002#s2",)
    with pytest.raises(ValidationError):
        exception_record(citations=[])


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [(date(2026, 9, 15), date(2026, 9, 18)), (date(2026, 9, 17), date(2026, 9, 22))],
)
def test_next_review_date(as_of, expected):
    assert next_review_date(as_of) == expected


def test_case_can_represent_failed_invoice_and_vendor_reads():
    case = Case(
        request=request(),
        as_of=datetime(2026, 9, 15, tzinfo=UTC),
        unknowns=[
            {"source": "get_invoice_document", "reason": "timeout"},
            {"source": "get_vendor_record", "reason": "timeout"},
        ],
    )
    assert case.invoice is None
    assert case.vendor is None
    assert len(case.unknowns) == 2


def test_final_result_has_every_section():
    assert set(FinalResult.model_fields) == {
        "facts",
        "calculations",
        "inferences",
        "assumptions",
        "unknowns",
        "findings",
        "exceptions",
        "actions_taken",
        "recommendation",
    }


@pytest.mark.parametrize("confidence", ["-0.01", "1.01", 0.5])
def test_confidence_is_an_exact_value_between_0_and_1(confidence):
    with pytest.raises(ValidationError):
        Recommendation(
            outcome="HOLD_FOR_INFORMATION",
            confidence=confidence,
            next_action="review",
            owner="AP Operations",
            rationale_source="TEMPLATE",
        )
