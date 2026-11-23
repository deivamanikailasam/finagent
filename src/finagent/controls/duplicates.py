"""Duplicate controls (FIN-POL-005 s1, s2), checked against paid, posted, held and rejected records."""

import re
import unicodedata
from decimal import Decimal

from finagent.config.clock import Clock
from finagent.controls.rules import rule
from finagent.domain.case import Case
from finagent.domain.enums import ExceptionCategory, HistoryStatus, Outcome
from finagent.domain.results import PolicyFinding
from finagent.tools.schemas import HistoryRecord

HISTORY_TOOL = "check_invoice_history"
DUPLICATE_RULES = frozenset({"DUP-EXACT-PAID", "DUP-EXACT-HELD", "DUP-PRIOR-REJECTED", "DUP-PROBABLE"})
_EXACT_RULE = {
    HistoryStatus.PAID: "DUP-EXACT-PAID",
    HistoryStatus.POSTED: "DUP-EXACT-PAID",
    HistoryStatus.HELD: "DUP-EXACT-HELD",
    HistoryStatus.REJECTED: "DUP-PRIOR-REJECTED",
}
_LEADING_ZEROS = re.compile(r"(?<![0-9])0+(?=[0-9])")
_PROCESS_CITATION = "FIN-POL-007#s5"


def normalise_invoice_number(number: str) -> str:
    """NFKC, casefold, drop punctuation and separators, and strip leading zeros in numeric runs."""
    characters = []
    for character in unicodedata.normalize("NFKC", number).casefold():
        category = unicodedata.category(character)
        if category[0] in {"P", "Z", "C"} or character == "−":  # U+2212 is a maths minus, not a dash
            continue
        characters.append(str(unicodedata.digit(character)) if category == "Nd" else character)
    return _LEADING_ZEROS.sub("", "".join(characters))


def _unknown(detail: str) -> list[PolicyFinding]:
    return [
        PolicyFinding(
            rule_id=rule_id,
            outcome="UNKNOWN",
            category=rule(rule_id).category,
            citations=rule(rule_id).citations,
            detail=detail,
        )
        for rule_id in sorted(DUPLICATE_RULES)
    ]


def _is_probable(case: Case, record: HistoryRecord, number: str) -> bool:
    invoice = case.invoice
    if normalise_invoice_number(record.invoice_no_norm) == number:
        return True
    hashes = {item.sha256 for item in (*invoice.attachments, *case.request.attachments)}
    if record.attachment_hash is not None and record.attachment_hash in hashes:
        return True
    if invoice.gross is None or invoice.invoice_date is None or record.gross == 0:
        return False
    if record.currency != invoice.currency:
        return False  # a percentage across two currencies has no meaning
    threshold = rule("DUP-PROBABLE").threshold
    variance = abs(invoice.gross - record.gross) / record.gross * Decimal(100)
    days = abs((invoice.invoice_date - record.invoice_date).days)
    return (
        variance < Decimal(threshold["amount_variance_percent_lt"])
        and days <= threshold["date_within_calendar_days"]
    )


def _fail(rule_id: str, invoice_no: str, record: HistoryRecord, po_ref: str | None) -> PolicyFinding:
    item = rule(rule_id)
    corroboration = (
        f"same PO {po_ref}" if po_ref is not None and record.po_number == po_ref else "no PO match"
    )
    return PolicyFinding(
        rule_id=rule_id,
        outcome=item.outcome_on_fail,
        category=item.category,
        owner=item.owner,
        citations=(*item.citations, f"HISTORY:{record.record_id}", f"INVOICE:{invoice_no}"),
        expected=record.gross,
        detail=f"matches {record.status} record; {corroboration}",
    )


def _prior_escalation(record: HistoryRecord, invoice_no: str) -> PolicyFinding:
    """A control review is not closed by resubmitting the invoice (decision log III, process rules)."""
    category = record.hold_category or ExceptionCategory.OTHER_CONTROL_RISK
    owner = (
        "Financial Control" if category == ExceptionCategory.AUTHORITY_GAP else "Financial Crime and Controls"
    )
    return PolicyFinding(
        rule_id="PRIOR-ESCALATION",
        outcome=Outcome.ESCALATE_CONTROL_REVIEW,
        category=category,
        owner=owner,
        citations=(_PROCESS_CITATION, f"HISTORY:{record.record_id}", f"INVOICE:{invoice_no}"),
        detail="the invoice was previously escalated",
    )


def check(case: Case, clock: Clock) -> list[PolicyFinding]:
    """Return one finding per failed record, or PASS or UNKNOWN for each duplicate rule."""
    invoice = case.invoice
    if invoice is None or invoice.invoice_no is None:
        return _unknown("invoice number unavailable")
    if any(item.source == HISTORY_TOOL for item in case.unknowns):
        return _unknown("history unavailable")  # no history is not the same as no duplicate
    number = normalise_invoice_number(invoice.invoice_no)
    failures: list[PolicyFinding] = []
    for record in case.history:
        if record.vendor_id != case.request.vendor_id:
            continue
        same_number = normalise_invoice_number(record.invoice_no_norm) == number
        if same_number and record.prior_outcome == Outcome.ESCALATE_CONTROL_REVIEW:
            failures.append(_prior_escalation(record, invoice.invoice_no))
        exact = same_number and record.currency == invoice.currency and record.gross == invoice.gross
        if exact:
            failures.append(_fail(_EXACT_RULE[record.status], invoice.invoice_no, record, invoice.po_ref))
        elif _is_probable(case, record, number):
            failures.append(_fail("DUP-PROBABLE", invoice.invoice_no, record, invoice.po_ref))
    failed = {item.rule_id for item in failures}
    passes = [
        PolicyFinding(rule_id=rule_id, outcome="PASS", citations=rule(rule_id).citations)
        for rule_id in sorted(DUPLICATE_RULES - failed)
    ]
    return [*failures, *passes]
