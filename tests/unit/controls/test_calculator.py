"""WP 5.1 calculator tests, written before the implementation."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from finagent.controls.calculator import calculate
from finagent.domain.results import Calculation
from fixtures.builder import build_case


def one_by(po_price, invoice_price, **extra):
    return {
        "po.lines[1].qty": 1,
        "po.lines[1].unit_price": po_price,
        "po.lines[1].line_value": po_price,
        "invoice.lines[1].qty": 1,
        "invoice.lines[1].unit_price": invoice_price,
        "po.receipts[1].qty": 1,
        "evidence.goods_receipt_note.lines[1].qty": 1,
        **extra,
    }


def services(invoice_price, confirmed="rcv-01"):
    return one_by(
        "3000.00",
        invoice_price,
        **{"po.lines[1].kind": "services", "po.receipts[1].service_confirmed_by": confirmed},
    )


def freight(invoice_price, permitted):
    return {
        "po.lines[2]": {
            "po_line_ref": "PO-L3",
            "kind": "freight",
            "qty": 1,
            "unit_price": "10.00",
            "line_value": "10.00",
            "qty_invoiced_to_date": 0,
        },
        "invoice.lines[2]": {
            "line_id": "L3",
            "po_line_ref": "PO-L3",
            "description_id": "DESC-FREIGHT",
            "qty": 1,
            "unit_price": invoice_price,
            "tax_code": "GST",
            "line_total": invoice_price,
        },
        "po.freight_permitted": permitted,
    }


def line(result, line_id):
    return next(item for item in result.line_checks if item.line_id == line_id)


def calc(overrides=None):
    return calculate(build_case("FIN-001", overrides))


@pytest.mark.parametrize(
    ("price", "within"),
    [("3030.00", True), ("3030.01", False), ("2970.00", True), ("2969.99", False)],
)
def test_goods_limit_is_the_lower_of_50_and_1_percent(price, within):
    check = line(calc(one_by("3000.00", price)), "L2")
    assert check.threshold == Decimal("30.00")
    assert check.within_tolerance is within


def test_goods_limit_uses_50_when_1_percent_is_higher():
    check = line(calc(one_by("9000.00", "9050.00")), "L2")
    assert (check.threshold, check.within_tolerance) == (Decimal("50.00"), True)
    assert line(calc(one_by("9000.00", "9050.01")), "L2").within_tolerance is False


def test_four_decimal_threshold_is_compared_at_full_precision():
    check = line(calc(one_by("1234.56", "1246.91")), "L2")
    assert check.threshold == Decimal("12.3456")
    assert check.variance == Decimal("12.35")
    assert check.within_tolerance is False


def test_variance_is_price_difference_times_invoiced_quantity():
    check = line(calc({"invoice.lines[1].unit_price": "30.20"}), "L2")
    assert check.variance == Decimal("20.00")
    assert check.threshold == Decimal("30.00")
    assert check.within_tolerance is True


@pytest.mark.parametrize(("price", "within"), [("3060.00", True), ("3060.01", False)])
def test_services_limit_is_the_lower_of_100_and_2_percent(price, within):
    check = line(calc(services(price)), "L2")
    assert check.threshold == Decimal("60.00")
    assert check.within_tolerance is within


def test_unconfirmed_service_is_not_within_tolerance():
    assert line(calc(services("3000.00", confirmed=None)), "L2").within_tolerance is False


@pytest.mark.parametrize(
    ("price", "permitted", "within"),
    [("85.00", True, True), ("85.01", True, False), ("85.00", False, False)],
)
def test_freight_tolerance(price, permitted, within):
    assert line(calc(freight(price, permitted)), "L3").within_tolerance is within


def test_freight_undercharge_beyond_the_limit_fails():
    overrides = freight("10.00", True)
    overrides["po.lines[2]"] = {**overrides["po.lines[2]"], "unit_price": "100.00", "line_value": "100.00"}
    assert line(calc(overrides), "L3").within_tolerance is False


def test_freight_needs_no_receipt_or_quantity_check():
    check = line(calc(freight("85.00", True)), "L3")
    assert (check.qty_ok, check.cumulative_ok) == (True, True)


def test_quantity_above_received_fails():
    check = line(calc({"invoice.lines[0].qty": 41}), "L1")
    assert check.qty_ok is False
    assert check.within_tolerance is False


def test_cumulative_quantity_fails():
    check = line(calc({"po.lines[0].qty_invoiced_to_date": 1}), "L1")
    assert (check.qty_ok, check.cumulative_ok) == (True, False)


def test_two_invoice_lines_on_one_po_line_are_added_together():
    extra = build_case("FIN-001").invoice.lines[0].model_dump(mode="json")
    result = calc({"invoice.lines[2]": {**extra, "line_id": "L3", "qty": 1, "line_total": "150.00"}})
    assert line(result, "L1").qty_ok is False
    assert line(result, "L3").qty_ok is False


def test_unmapped_line():
    extra = build_case("FIN-001").invoice.lines[0].model_dump(mode="json")
    result = calc({"invoice.lines[2]": {**extra, "line_id": "L3", "po_line_ref": "PO-L9"}})
    assert line(result, "L3").mapped is False


@pytest.mark.parametrize(
    ("tax", "ok", "rounding"),
    [("900.00", True, "0.00"), ("900.01", True, "0.01"), ("900.02", False, "0.02")],
)
def test_tax_check(tax, ok, rounding):
    check = calc({"invoice.tax": tax}).tax_check
    assert check.ok is ok
    assert check.rounding_adjustment == Decimal(rounding)


def test_missing_tax_fails():
    assert calc({"invoice.tax": None}).tax_check.ok is False


def test_every_record_is_complete():
    result = calc()
    assert result.records
    for record in result.records:
        assert isinstance(record, Calculation)
        assert record.id and record.inputs and record.formula
        assert record.result is not None
        assert record.rounding_method == "ROUND_HALF_UP"


def test_gross_record():
    record = next(item for item in calc().records if item.id == "CALC-GROSS")
    assert record.result == Decimal("9900.00")


def test_document_total_check():
    assert calc().document_total_check.ok is True
    assert calc({"invoice.gross": "9990.00"}).document_total_check.ok is False


def test_floats_are_rejected():
    with pytest.raises(ValidationError):
        Calculation(id="C", inputs={"a": 1.5}, formula="a", result="1", rounding_method="ROUND_HALF_UP")
    with pytest.raises(ValidationError):
        build_case("FIN-001", {"invoice.lines[0].unit_price": 150.0})


def test_negative_lines_are_excluded_from_tolerance_and_quantity():
    check = line(calc({"invoice.lines[1].unit_price": "-30.00"}), "L2")
    assert check.negative is True
    assert (check.within_tolerance, check.qty_ok, check.cumulative_ok) == (True, True, True)


def test_unavailable_po_has_no_line_checks():
    assert calculate(build_case("FIN-004")).line_checks == ()
