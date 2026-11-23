"""Invoice evidence, three-way matching and receipt rules (decision log III)."""

from decimal import Decimal
from typing import Any

from finagent.config.clock import Clock, local_date
from finagent.controls.calculator import (
    CalcResult,
    LineCheck,
    line_total_is_correct,
)
from finagent.controls.rules import rule
from finagent.domain.case import Case
from finagent.domain.results import PolicyFinding

GRN_CITATION = "CASE:goods_receipt_note"
PO_TOOL = "get_purchase_order"
NORTHSTAR_ENTITIES = frozenset({"northstar group pty ltd"})
HEADER_FIELDS = ("legal_name", "invoice_no", "invoice_date", "currency", "gross")
INVOICE_RULES = ("INV-HEADER", "INV-BILL-TO", "REQ-MATCHES-INVOICE", "TAX-CHECK", "NEGATIVE-LINE")
PO_DEPENDENT_RULES = (
    "PO-APPROVED",
    "SERVICE-CONFIRMED",
    "TYPED-RECORD-PRECEDENCE",
    "TOL-GOODS",
    "TOL-SERVICES",
    "TOL-FREIGHT",
    "QTY-RECEIVED",
    "QTY-CUMULATIVE",
    "LINE-MAPPED",
)
MATCHING_RULES = frozenset({*INVOICE_RULES, *PO_DEPENDENT_RULES, "PO-REQUIRED", "RECEIPT-REQUIRED"})


def _normal(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


def _owner_known(case: Case, owner: str) -> bool:
    if "receipter" in owner:
        return case.po is not None and bool(case.po.receipts)
    return case.po is not None if owner == "requester" else True


def _fail(case: Case, rule_id: str, *extra_citations: str, **fields: Any) -> PolicyFinding:
    item = rule(rule_id)
    return PolicyFinding(
        rule_id=rule_id,
        outcome=item.outcome_on_fail,
        category=item.category,
        owner=item.owner,
        owner_known=_owner_known(case, item.owner),
        citations=(*item.citations, *extra_citations),
        **fields,
    )


def _pass(rule_id: str, *extra_citations: str) -> PolicyFinding:
    return PolicyFinding(
        rule_id=rule_id, outcome="PASS", citations=(*rule(rule_id).citations, *extra_citations)
    )


def _unknown(rule_id: str, detail: str) -> PolicyFinding:
    item = rule(rule_id)
    return PolicyFinding(
        rule_id=rule_id, outcome="UNKNOWN", category=item.category, citations=item.citations, detail=detail
    )


def _or_pass(failures: list[PolicyFinding], rule_id: str, *citations: str) -> list[PolicyFinding]:
    return failures or [_pass(rule_id, *citations)]


def _invoice_findings(case: Case, calc: CalcResult) -> list[PolicyFinding]:
    invoice = case.invoice
    findings: list[PolicyFinding] = []
    missing = [name for name in HEADER_FIELDS if getattr(invoice, name) is None]
    findings.append(
        _fail(case, "INV-HEADER", detail=f"missing: {', '.join(missing)}") if missing else _pass("INV-HEADER")
    )
    bill_to_ok = _normal(invoice.bill_to_entity) in NORTHSTAR_ENTITIES
    findings.append(
        _pass("INV-BILL-TO") if bill_to_ok else _fail(case, "INV-BILL-TO", detail="bill-to entity")
    )
    findings.extend(_or_pass(_request_failures(case, calc), "REQ-MATCHES-INVOICE"))
    tax = calc.tax_check
    if tax.ok:
        findings.append(_pass("TAX-CHECK"))
    else:
        findings.append(
            _fail(
                case,
                "TAX-CHECK",
                expected=tax.expected,
                actual=tax.stated,
                difference=tax.rounding_adjustment,
                threshold=Decimal(rule("TAX-CHECK").threshold["rounding_allowance"]),
            )
        )
    negative = [
        _fail(case, "NEGATIVE-LINE", line_ref=line.line_id, actual=line.line_total)
        for line in invoice.lines
        if line.qty < 0 or line.unit_price < 0 or line.line_total < 0
    ]
    findings.extend(_or_pass(negative, "NEGATIVE-LINE"))
    return findings


def _request_failures(case: Case, calc: CalcResult) -> list[PolicyFinding]:
    invoice, request, vendor = case.invoice, case.request, case.vendor
    failures: list[PolicyFinding] = []
    if invoice.gross is None or request.amount != invoice.gross:
        difference = None if invoice.gross is None else request.amount - invoice.gross
        failures.append(
            _fail(
                case,
                "REQ-MATCHES-INVOICE",
                detail="request amount",
                expected=invoice.gross,
                actual=request.amount,
                difference=difference,
            )
        )
    if request.currency != invoice.currency:
        failures.append(
            _fail(
                case,
                "REQ-MATCHES-INVOICE",
                detail="request currency",
                expected=invoice.currency,
                actual=request.currency,
            )
        )
    total = calc.document_total_check
    if invoice.gross is not None and not total.ok:
        failures.append(
            _fail(
                case,
                "REQ-MATCHES-INVOICE",
                detail="document total",
                expected=total.expected,
                actual=total.stated,
                difference=total.stated - total.expected,
            )
        )
    for line in invoice.lines:
        if not line_total_is_correct(line):
            failures.append(
                _fail(
                    case,
                    "REQ-MATCHES-INVOICE",
                    line_ref=line.line_id,
                    detail="line total differs from quantity x unit price",
                    expected=Decimal(line.qty) * line.unit_price,
                    actual=line.line_total,
                )
            )
    if vendor is not None:
        if request.vendor_id != vendor.vendor_id:
            failures.append(_fail(case, "REQ-MATCHES-INVOICE", detail="request vendor"))
        if invoice.legal_name is not None and _normal(invoice.legal_name) != _normal(vendor.legal_name):
            failures.append(
                _fail(case, "REQ-MATCHES-INVOICE", detail="legal name differs from vendor master")
            )
    return failures


def _price_failure(case: Case, rule_id: str, check: LineCheck, po_citation: str) -> PolicyFinding:
    return _fail(
        case,
        rule_id,
        po_citation,
        line_ref=check.line_id,
        expected=check.po_unit_price,
        actual=check.invoice_unit_price,
        difference=check.variance,
        threshold=check.threshold,
    )


def _receipt_findings(case: Case, checks: tuple[LineCheck, ...], po_citation: str) -> list[PolicyFinding]:
    po = case.po
    typed: dict[str, int] = {}
    for receipt in po.receipts:
        typed[receipt.po_line_ref] = typed.get(receipt.po_line_ref, 0) + receipt.qty
    missing = [
        _fail(case, "RECEIPT-REQUIRED", po_citation, line_ref=check.line_id, detail="no typed receipt")
        for check in checks
        if check.kind == "goods" and not check.negative and check.po_line_ref not in typed
    ]
    unconfirmed = [
        _fail(
            case, "SERVICE-CONFIRMED", po_citation, line_ref=check.line_id, detail="no service confirmation"
        )
        for check in checks
        if check.kind == "services" and not check.negative and not check.service_confirmed
    ]
    note = case.evidence.goods_receipt_note
    disagreements: list[PolicyFinding] = []
    citations: tuple[str, ...] = (po_citation,)
    if note is not None:
        citations = (GRN_CITATION, po_citation)
        noted = {line.po_line_ref: line.qty for line in note.lines}
        for ref, qty in typed.items():
            if noted.get(ref) != qty:
                disagreements.append(
                    _fail(
                        case,
                        "TYPED-RECORD-PRECEDENCE",
                        *citations,
                        line_ref=ref,
                        expected=qty,
                        actual=noted.get(ref),
                    )
                )
    today = local_date(case.as_of)
    disagreements.extend(
        _fail(
            case,
            "TYPED-RECORD-PRECEDENCE",
            *citations,
            line_ref=receipt.po_line_ref,
            detail="receipt is dated after the case date",
        )
        for receipt in po.receipts
        if receipt.date > today
    )
    return [
        *_or_pass(missing, "RECEIPT-REQUIRED", *citations),
        *_or_pass(unconfirmed, "SERVICE-CONFIRMED", po_citation),
        *_or_pass(disagreements, "TYPED-RECORD-PRECEDENCE", *citations),
    ]


def _line_findings(case: Case, checks: tuple[LineCheck, ...], po_citation: str) -> list[PolicyFinding]:
    price_rules = {"goods": "TOL-GOODS", "services": "TOL-SERVICES", "freight": "TOL-FREIGHT"}
    findings: list[PolicyFinding] = []
    for kind, rule_id in price_rules.items():
        failed = [
            _price_failure(case, rule_id, check, po_citation)
            for check in checks
            if check.kind == kind and not check.price_ok
        ]
        findings.extend(_or_pass(failed, rule_id, po_citation))
    received = [
        _fail(case, "QTY-RECEIVED", po_citation, line_ref=check.line_id)
        for check in checks
        if not check.qty_ok
    ]
    cumulative = [
        _fail(case, "QTY-CUMULATIVE", po_citation, line_ref=check.line_id)
        for check in checks
        if not check.cumulative_ok
    ]
    unmapped = [
        _fail(case, "LINE-MAPPED", po_citation, line_ref=check.line_id, actual=check.po_line_ref)
        for check in checks
        if not check.mapped
    ]
    return [
        *findings,
        *_or_pass(received, "QTY-RECEIVED", po_citation),
        *_or_pass(cumulative, "QTY-CUMULATIVE", po_citation),
        *_or_pass(unmapped, "LINE-MAPPED", po_citation),
    ]


def check(case: Case, calc: CalcResult, clock: Clock) -> list[PolicyFinding]:
    """Return one finding per evaluated rule: PASS, UNKNOWN, or the rule's outcome per failed item."""
    if case.invoice is None:
        # A failed read is missing evidence: it holds the case and never reads as an invalid invoice.
        return [_unknown(rule_id, "invoice unavailable") for rule_id in sorted(MATCHING_RULES)]
    findings = _invoice_findings(case, calc)
    if case.invoice.po_ref is None:
        return [*findings, _fail(case, "PO-REQUIRED", detail="no PO reference")]
    findings.append(_pass("PO-REQUIRED"))
    if case.po is None:
        reason = (
            "PO tool failed" if any(item.source == PO_TOOL for item in case.unknowns) else "PO unavailable"
        )
        findings.extend(_unknown(rule_id, reason) for rule_id in PO_DEPENDENT_RULES)
        findings.append(_fail(case, "RECEIPT-REQUIRED", detail=f"no typed receipt: {reason}"))
        return findings
    po_citation = f"PO:{case.po.po_ref}"
    if case.po.approval_status != "APPROVED" or case.po.po_ref != case.invoice.po_ref:
        findings.append(_fail(case, "PO-APPROVED", po_citation, actual=case.po.approval_status))
    else:
        findings.append(_pass("PO-APPROVED", po_citation))
    return [
        *findings,
        *_receipt_findings(case, calc.line_checks, po_citation),
        *_line_findings(case, calc.line_checks, po_citation),
    ]
