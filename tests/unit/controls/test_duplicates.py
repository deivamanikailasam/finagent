"""WP 5.2 duplicate-control tests, written before the implementation."""

import pytest

from finagent.config.clock import FixedClock
from finagent.controls.duplicates import check, normalise_invoice_number
from finagent.domain.results import Unknown
from fixtures.builder import build_case, load_unit_fixture

PASS_STATES = {"PASS", "UNKNOWN"}
SHA = "a" * 64


def record(number="inv1001", gross="9900.00", day="2026-09-01", status="PAID", **extra):
    return {
        "record_id": "H-0001",
        "status": status,
        "vendor_id": "V-100",
        "invoice_no_norm": number,
        "currency": "AUD",
        "gross": gross,
        "invoice_date": day,
        "po_number": "PO-9999",
        **extra,
    }


def run(case):
    return check(case, FixedClock(case.as_of))


def failures(case):
    return [item for item in run(case) if item.outcome not in PASS_STATES]


def with_history(**fields):
    return build_case("FIN-001", {"history[0]": record(**fields)})


def only(case):
    found = failures(case)
    assert len(found) == 1, [item.rule_id for item in found]
    return found[0]


def test_fin_001_has_no_duplicate():
    assert failures(build_case("FIN-001")) == []


def test_fin_002_is_reject_duplicate_citing_the_history_record():
    item = only(build_case("FIN-002"))
    assert (item.rule_id, item.outcome, item.category) == (
        "DUP-EXACT-PAID",
        "REJECT_DUPLICATE",
        "DUPLICATE_RISK",
    )
    assert "HISTORY:H-0001" in item.citations
    assert "FIN-POL-005#s2" in item.citations


def test_posted_record_is_also_reject_duplicate():
    assert only(with_history(status="POSTED")).outcome == "REJECT_DUPLICATE"


@pytest.mark.parametrize("number", ["INV‐1001", "inv 1001", "INV-01001", "INV–1001", "INV−1001"])
def test_number_variants_are_exact_matches(number):
    case = build_case(
        "FIN-001",
        {"invoice.invoice_no": number, "request.invoice_ref": number, "history[0]": record()},
    )
    assert only(case).rule_id == "DUP-EXACT-PAID"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("INV-1001", "inv1001"),
        ("INV‐1001", "inv1001"),
        ("inv 1001", "inv1001"),
        ("INV-01001", "inv1001"),
        ("INV/00-1", "inv1"),
        ("0000", "0"),
        ("A 1", "a1"),
    ],
)
def test_normalisation(raw, expected):
    assert normalise_invoice_number(raw) == expected


def test_unicode_hyphen_fixture_is_an_exact_match():
    assert only(load_unit_fixture("unicode_hyphen_duplicate")).rule_id == "DUP-EXACT-PAID"


def test_exact_match_to_held_is_hold():
    item = only(with_history(status="HELD"))
    assert (item.rule_id, item.outcome, item.category) == (
        "DUP-EXACT-HELD",
        "HOLD_FOR_INFORMATION",
        "DUPLICATE_RISK",
    )


def test_exact_match_to_rejected_is_hold_not_auto_reject():
    item = only(with_history(status="REJECTED"))
    assert (item.rule_id, item.outcome, item.category) == (
        "DUP-PRIOR-REJECTED",
        "HOLD_FOR_INFORMATION",
        "DUPLICATE_RISK",
    )


def test_probable_duplicate_cites_both_records():
    item = only(with_history(number="inv1009", gross="9880.00", day="2026-08-25"))
    assert (item.rule_id, item.outcome, item.category) == (
        "DUP-PROBABLE",
        "HOLD_FOR_INFORMATION",
        "DUPLICATE_RISK",
    )
    assert {"HISTORY:H-0001", "INVOICE:INV-1001"} <= set(item.citations)


def test_amount_outside_the_band_is_not_probable():
    assert failures(with_history(number="inv1009", gross="9840.00", day="2026-08-25")) == []


@pytest.mark.parametrize(("gross", "probable"), [("9851.00", True), ("9850.00", False)])
def test_amount_boundary_pair(gross, probable):
    found = failures(with_history(number="inv1009", gross=gross, day="2026-08-25"))
    assert bool(found) is probable


@pytest.mark.parametrize(("day", "probable"), [("2026-08-18", True), ("2026-08-17", False)])
def test_date_boundary_pair(day, probable):
    found = failures(with_history(number="inv1009", gross="9880.00", day=day))
    assert bool(found) is probable


def test_date_window_applies_in_both_directions():
    assert failures(with_history(number="inv1009", gross="9880.00", day="2026-09-15"))
    assert not failures(with_history(number="inv1009", gross="9880.00", day="2026-09-16"))


def test_same_stripped_number_with_a_different_amount_is_probable():
    assert only(with_history(gross="5000.00", day="2026-01-01")).rule_id == "DUP-PROBABLE"


def test_same_attachment_hash_is_probable():
    attachment = {"ref": "A1", "sha256": SHA, "origin": "supplier"}
    case = build_case(
        "FIN-001",
        {
            "invoice.attachments[0]": attachment,
            "history[0]": record(number="inv7777", gross="100.00", day="2026-01-01", attachment_hash=SHA),
        },
    )
    assert only(case).rule_id == "DUP-PROBABLE"


def test_two_missing_attachment_hashes_do_not_match():
    assert failures(with_history(number="inv7777", gross="100.00", day="2026-01-01")) == []


def test_po_match_alone_is_no_finding():
    case = with_history(number="inv7777", gross="100.00", day="2026-01-01", po_number="PO-4471")
    assert failures(case) == []


def test_po_match_is_recorded_as_corroboration():
    case = with_history(number="inv1009", gross="9880.00", day="2026-08-25", po_number="PO-4471")
    assert "PO-4471" in only(case).detail


def test_other_vendor_is_not_a_duplicate():
    assert failures(with_history(vendor_id="V-200")) == []


def test_other_currency_is_not_an_exact_match():
    assert only(with_history(currency="USD")).rule_id == "DUP-PROBABLE"


def test_exact_paid_outranks_a_probable_record_for_the_same_row():
    assert [item.rule_id for item in failures(with_history())] == ["DUP-EXACT-PAID"]


def test_prior_escalation_on_the_same_invoice_escalates_again():
    case = with_history(status="HELD", prior_outcome="ESCALATE_CONTROL_REVIEW", hold_category="BANK_CHANGE")
    found = failures(case)
    escalated = [item for item in found if item.outcome == "ESCALATE_CONTROL_REVIEW"]
    assert len(escalated) == 1
    assert escalated[0].category == "BANK_CHANGE"
    assert "HISTORY:H-0001" in escalated[0].citations


def test_prior_escalation_on_another_invoice_does_not_escalate():
    case = with_history(
        number="inv7777",
        gross="100.00",
        day="2026-01-01",
        status="HELD",
        prior_outcome="ESCALATE_CONTROL_REVIEW",
    )
    assert failures(case) == []


def test_history_tool_failure_is_unknown():
    case = build_case("FIN-001").model_copy(
        update={"unknowns": (Unknown(source="check_invoice_history", reason="timeout"),)}
    )
    findings = run(case)
    assert findings
    assert {item.outcome for item in findings} == {"UNKNOWN"}


def test_failed_invoice_read_is_unknown():
    case = build_case("FIN-001").model_copy(update={"invoice": None})
    assert {item.outcome for item in run(case)} == {"UNKNOWN"}


def test_missing_invoice_number_is_unknown_not_a_duplicate():
    case = build_case("FIN-001", {"invoice.invoice_no": None, "history[0]": record()})
    assert {item.outcome for item in run(case)} == {"UNKNOWN"}
