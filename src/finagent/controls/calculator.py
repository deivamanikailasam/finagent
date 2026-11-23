"""Deterministic invoice arithmetic. Decimal at full precision; rounding only for display and posting."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from finagent.controls.rules import rule
from finagent.domain.case import Case
from finagent.domain.results import Calculation
from finagent.domain.types import CENT
from finagent.tools.schemas import InvoiceLine, PoLine, PurchaseOrder

ROUNDING = "ROUND_HALF_UP"
TAX_RATES = {"GST": Decimal("0.10"), "FRE": Decimal("0")}
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class LineCheck:
    line_id: str
    po_line_ref: str | None
    kind: str | None
    mapped: bool
    negative: bool
    within_tolerance: bool
    price_ok: bool
    threshold: Decimal | None
    variance: Decimal | None
    qty_ok: bool
    cumulative_ok: bool
    service_confirmed: bool
    invoice_unit_price: Decimal
    po_unit_price: Decimal | None


@dataclass(frozen=True)
class TaxCheck:
    ok: bool
    expected: Decimal
    stated: Decimal | None
    rounding_adjustment: Decimal | None


@dataclass(frozen=True)
class DocumentTotalCheck:
    ok: bool
    expected: Decimal
    stated: Decimal | None


@dataclass(frozen=True)
class CalcResult:
    records: tuple[Calculation, ...]
    line_checks: tuple[LineCheck, ...]
    tax_check: TaxCheck
    document_total_check: DocumentTotalCheck


def _record(record_id: str, inputs: dict[str, Decimal], formula: str, result: Decimal) -> Calculation:
    return Calculation(id=record_id, inputs=inputs, formula=formula, result=result, rounding_method=ROUNDING)


def _limit(absolute_rule: str, line_value: Decimal) -> Decimal:
    threshold = rule(absolute_rule).threshold
    percent = Decimal(threshold["percent_of_po_line"]) / HUNDRED
    return min(Decimal(threshold["absolute"]), line_value * percent)


def _is_negative(line: InvoiceLine) -> bool:
    return line.qty < 0 or line.unit_price < 0 or line.line_total < 0


def line_total_is_correct(line: InvoiceLine) -> bool:
    """The billed line total must equal quantity times unit price, to the cent."""
    return (Decimal(line.qty) * line.unit_price).quantize(CENT, ROUND_HALF_UP) == line.line_total


def _unmapped(line: InvoiceLine, negative: bool) -> LineCheck:
    # An unmapped line is reported by LINE-MAPPED alone: it has no PO price or quantity to compare.
    return LineCheck(
        line.line_id, line.po_line_ref, None, False, negative, True, True, None, None, True, True, True,
        line.unit_price, None,
    )  # fmt: skip


def _check_line(
    line: InvoiceLine,
    po_line: PoLine,
    po: PurchaseOrder,
    invoiced: int,
    received: int,
    confirmed: bool,
    line_variance_total: Decimal,
    billed_total: Decimal,
) -> LineCheck:
    if _is_negative(line):
        # Negative lines are excluded from tolerance and quantity checks (decision log III).
        return LineCheck(
            line.line_id, line.po_line_ref, po_line.kind, True, True, True, True, None, None, True, True,
            confirmed, line.unit_price, po_line.unit_price,
        )  # fmt: skip
    variance = (line.unit_price - po_line.unit_price) * Decimal(line.qty)
    if po_line.kind == "freight":
        limit = Decimal(rule("TOL-FREIGHT").threshold["absolute"]) if po.freight_permitted else Decimal("0")
        # Freight is a value, so the whole amount billed on the PO line is compared with its PO value.
        variance = billed_total - po_line.line_value
        price_ok = abs(variance) <= limit
        return LineCheck(
            line.line_id, line.po_line_ref, "freight", True, False, price_ok, price_ok, limit, variance, True,
            True, True, line.unit_price, po_line.unit_price,
        )  # fmt: skip
    limit = _limit("TOL-SERVICES" if po_line.kind == "services" else "TOL-GOODS", po_line.line_value)
    # Each line and the PO line as a whole must be in tolerance, so a variance cannot be split.
    price_ok = abs(variance) <= limit and abs(line_variance_total) <= limit
    allowed = min(received, po_line.qty)  # a receipt above the ordered quantity is not payable
    qty_ok = invoiced <= allowed
    cumulative_ok = po_line.qty_invoiced_to_date + invoiced <= allowed
    within = price_ok and (confirmed if po_line.kind == "services" else qty_ok)
    return LineCheck(
        line.line_id, line.po_line_ref, po_line.kind, True, False, within, price_ok, limit, variance, qty_ok,
        cumulative_ok, confirmed, line.unit_price, po_line.unit_price,
    )  # fmt: skip


def _line_checks(case: Case) -> tuple[LineCheck, ...]:
    po, invoice = case.po, case.invoice
    if po is None or invoice is None:
        return ()
    counts = Counter(line.po_line_ref for line in po.lines)
    po_lines = {line.po_line_ref: line for line in po.lines if counts[line.po_line_ref] == 1}
    invoiced: dict[str | None, int] = defaultdict(int)
    variance_total: dict[str | None, Decimal] = defaultdict(Decimal)
    billed: dict[str | None, Decimal] = defaultdict(Decimal)
    for line in invoice.lines:
        if not _is_negative(line):
            invoiced[line.po_line_ref] += line.qty  # lines sharing a PO line are checked together
            billed[line.po_line_ref] += line.line_total
            if line.po_line_ref in po_lines:
                unit_gap = line.unit_price - po_lines[line.po_line_ref].unit_price
                variance_total[line.po_line_ref] += unit_gap * Decimal(line.qty)
    received: dict[str, int] = defaultdict(int)
    for receipt in po.receipts:
        received[receipt.po_line_ref] += receipt.qty
    checks = []
    for line in invoice.lines:
        po_line = po_lines.get(line.po_line_ref)
        if po_line is None:
            checks.append(_unmapped(line, _is_negative(line)))
            continue
        receipts = [item for item in po.receipts if item.po_line_ref == line.po_line_ref]
        confirmed = bool(receipts) and all(item.service_confirmed_by for item in receipts)
        checks.append(
            _check_line(
                line,
                po_line,
                po,
                invoiced[line.po_line_ref],
                received[line.po_line_ref],
                confirmed,
                variance_total[line.po_line_ref],
                billed[line.po_line_ref],
            )
        )
    return tuple(checks)


def calculate(case: Case) -> CalcResult:
    invoice = case.invoice
    if invoice is None:
        zero = Decimal("0")
        return CalcResult((), (), TaxCheck(False, zero, None, None), DocumentTotalCheck(False, zero, None))
    subtotal = sum((line.line_total for line in invoice.lines), Decimal("0"))
    computed_tax = sum((line.line_total * TAX_RATES[line.tax_code] for line in invoice.lines), Decimal("0"))
    allowance = Decimal(rule("TAX-CHECK").threshold["rounding_allowance"])
    adjustment = None if invoice.tax is None else invoice.tax - computed_tax
    tax_check = TaxCheck(
        ok=adjustment is not None and abs(adjustment) <= allowance,
        expected=computed_tax,
        stated=invoice.tax,
        rounding_adjustment=adjustment,
    )
    tax_for_total = invoice.tax if invoice.tax is not None else computed_tax.quantize(CENT, ROUND_HALF_UP)
    expected_gross = subtotal + tax_for_total
    total_check = DocumentTotalCheck(invoice.gross == expected_gross, expected_gross, invoice.gross)
    line_checks = _line_checks(case)
    records = [
        _record("CALC-SUBTOTAL", {"line_count": Decimal(len(invoice.lines))}, "sum(line_total)", subtotal),
        _record("CALC-TAX", {"subtotal": subtotal}, "sum(line_total x tax_rate)", computed_tax),
        _record("CALC-GROSS", {"subtotal": subtotal, "tax": tax_for_total}, "subtotal + tax", expected_gross),
    ]
    for check in line_checks:
        if check.variance is not None and check.po_unit_price is not None and check.threshold is not None:
            inputs = {"invoice_unit_price": check.invoice_unit_price, "po_unit_price": check.po_unit_price}
            formula = "(invoice_unit_price - po_unit_price) x invoiced_qty"
            records.append(_record(f"CALC-VARIANCE-{check.line_id}", inputs, formula, check.variance))
            records.append(
                _record(
                    f"CALC-THRESHOLD-{check.line_id}",
                    {"threshold": check.threshold},
                    f"tolerance limit for {check.kind}",
                    check.threshold,
                )
            )
    return CalcResult(tuple(records), line_checks, tax_check, total_check)
