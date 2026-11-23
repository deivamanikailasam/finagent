"""WP 5.1 matching tests, written before the implementation."""

from decimal import Decimal

import pytest

from finagent.config.clock import FixedClock
from finagent.controls.calculator import calculate
from finagent.controls.matching import check
from fixtures.builder import build_case, load_unit_fixture

PASS_STATES = {"PASS", "UNKNOWN"}


def run(case):
    return check(case, calculate(case), FixedClock(case.as_of))


def failures(findings):
    return [item for item in findings if item.outcome not in PASS_STATES]


def failed_rules(case):
    return {item.rule_id for item in failures(run(case))}


def categories(case):
    return {item.category for item in failures(run(case))}


def finding(findings, rule_id):
    return next(item for item in failures(findings) if item.rule_id == rule_id)


def test_fin_001_has_no_failures():
    assert failures(run(build_case("FIN-001"))) == []


def test_pass_findings_carry_their_citations():
    findings = run(build_case("FIN-001"))
    cited = {citation for item in findings for citation in item.citations}
    assert {"FIN-POL-001#s5", "FIN-POL-002#s2", "CASE:goods_receipt_note", "PO:PO-4471"} <= cited


def test_supplier_written_grn_is_not_a_receipt():
    case = load_unit_fixture("supplier_written_grn_no_receipt")
    assert "RECEIPT-REQUIRED" in failed_rules(case)
    assert "MISSING_RECEIPT" in categories(case)


def test_grn_contradicting_the_typed_receipt_cites_both():
    item = finding(run(load_unit_fixture("grn_contradicts_receipt")), "TYPED-RECORD-PRECEDENCE")
    assert item.category == "OTHER_CONTROL_RISK"
    assert {"CASE:goods_receipt_note", "PO:PO-4471"} <= set(item.citations)
    assert (item.line_ref, item.expected, item.actual) == ("PO-L1", 40, 39)


def test_fin_004_is_missing_receipt_and_never_reject_invalid():
    findings = run(build_case("FIN-004"))
    assert {item.rule_id for item in failures(findings)} == {"RECEIPT-REQUIRED"}
    item = finding(findings, "RECEIPT-REQUIRED")
    assert (item.category, item.owner_known) == ("MISSING_RECEIPT", False)
    assert all(result.outcome != "REJECT_INVALID" for result in findings)
    unknown = {result.rule_id for result in findings if result.outcome == "UNKNOWN"}
    assert {"PO-APPROVED", "TOL-GOODS", "QTY-RECEIVED", "LINE-MAPPED"} <= unknown


def test_no_po_reference_is_missing_po():
    case = build_case("FIN-001", {"invoice.po_ref": None, "po": None})
    assert failed_rules(case) == {"PO-REQUIRED"}
    assert categories(case) == {"MISSING_PO"}


def test_draft_po_is_missing_po():
    case = build_case("FIN-001", {"po.approval_status": "DRAFT"})
    assert failed_rules(case) == {"PO-APPROVED"}
    assert categories(case) == {"MISSING_PO"}


def test_po_for_a_different_reference_fails():
    assert "PO-APPROVED" in failed_rules(build_case("FIN-001", {"invoice.po_ref": "PO-9999"}))


@pytest.mark.parametrize("field", ["invoice_no", "legal_name", "invoice_date", "currency", "gross"])
def test_missing_header_field_is_reject_invalid(field):
    item = finding(run(build_case("FIN-001", {f"invoice.{field}": None})), "INV-HEADER")
    assert item.outcome == "REJECT_INVALID"
    assert item.category == "OTHER_CONTROL_RISK"


def test_bill_to_another_entity_is_reject_invalid():
    case = build_case("FIN-001", {"invoice.bill_to_entity": "Example Holdings Pty Ltd"})
    assert finding(run(case), "INV-BILL-TO").outcome == "REJECT_INVALID"


def test_request_amount_differs():
    case = build_case("FIN-001", {"request.amount": "9950.00"})
    item = finding(run(case), "REQ-MATCHES-INVOICE")
    assert item.category == "OTHER_CONTROL_RISK"
    assert item.difference == Decimal("50.00")


@pytest.mark.parametrize(
    "overrides",
    [
        {"invoice.gross": "9990.00"},
        {"invoice.legal_name": "Different Supplier Pty Ltd"},
        {"request.vendor_id": "V-200"},
        {"request.currency": "USD"},
    ],
)
def test_request_and_invoice_must_agree(overrides):
    assert "REQ-MATCHES-INVOICE" in failed_rules(build_case("FIN-001", overrides))


def test_negative_line_is_other_control_risk_only():
    case = build_case("FIN-001", {"invoice.lines[1].unit_price": "-30.00"})
    assert failed_rules(case) == {"NEGATIVE-LINE"}
    assert categories(case) == {"OTHER_CONTROL_RISK"}


def test_price_failure_records_the_failed_line_and_threshold():
    case = build_case("FIN-001", {"invoice.lines[1].unit_price": "30.31"})
    item = finding(run(case), "TOL-GOODS")
    assert (item.line_ref, item.category) == ("L2", "PRICE_VARIANCE")
    assert item.expected == Decimal("30.00")
    assert item.actual == Decimal("30.31")
    assert item.difference == Decimal("31.00")
    assert item.threshold == Decimal("30.00")


def test_quantity_failure_does_not_add_a_price_failure():
    case = build_case("FIN-001", {"invoice.lines[0].qty": 41})
    assert "QTY-RECEIVED" in failed_rules(case)
    assert "TOL-GOODS" not in failed_rules(case)


def test_unconfirmed_service_does_not_add_a_price_failure():
    case = build_case("FIN-001", {"po.lines[1].kind": "services"})
    assert failed_rules(case) == {"SERVICE-CONFIRMED"}


def test_second_invoice_on_a_fully_invoiced_line_fails():
    assert "QTY-CUMULATIVE" in failed_rules(load_unit_fixture("second_invoice_fully_invoiced_line"))


def test_tax_mismatch():
    case = build_case("FIN-001", {"invoice.tax": "905.00"})
    assert finding(run(case), "TAX-CHECK").category == "TAX_QUERY"


def test_failed_invoice_read_is_unknown_never_a_failure():
    case = build_case("FIN-001").model_copy(update={"invoice": None})
    findings = run(case)
    assert failures(findings) == []
    assert {item.outcome for item in findings} == {"UNKNOWN"}
    assert "INV-HEADER" in {item.rule_id for item in findings}


def test_every_failure_has_owner_citation_and_category():
    case = build_case(
        "FIN-001",
        {"invoice.lines[1].unit_price": "31.00", "invoice.tax": "905.00", "po.approval_status": "DRAFT"},
    )
    for item in failures(run(case)):
        assert item.owner and item.citations and item.category
