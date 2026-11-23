"""Vendor status, bank-change, delegated-authority, high-risk, segregation and currency controls."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from finagent.config.clock import Clock, local_date, window_end
from finagent.controls.rules import rule
from finagent.domain.case import Case
from finagent.domain.enums import VendorStatus
from finagent.domain.results import PolicyFinding
from finagent.rag.loader import IngestResult, ingest, is_authoritative

ROOT = Path(__file__).resolve().parents[3]
DFA_DOC = "FIN-POL-003"
HOME_CURRENCY = "AUD"
HOME_COUNTRY = "AU"
AUTHORITY_TOOL = "get_authority_record"
AuthoritySource = Callable[[str, datetime], bool]
VENDOR_RULES = (
    "VENDOR-STATUS",
    "BANK-CHANGE-HOLD",
    "REMIT-MATCHES-MASTER",
    "FIRST-PAYMENT-AFTER-BANK-CHANGE",
    "HIGH-RISK",
)
AMOUNT_RULES = ("DFA-TIER", "DFA-AUTHORITATIVE-SOURCE", "SOD-ABOVE-25K")
VENDOR_AUTHORITY_RULES = frozenset({*VENDOR_RULES, *AMOUNT_RULES, "FX-HOLD"})


@dataclass(frozen=True)
class VAResult:
    findings: tuple[PolicyFinding, ...]
    dfa_role: str | None


@lru_cache(maxsize=1)
def _corpus() -> IngestResult:
    return ingest(ROOT / "corpus", ROOT / "config/registry.yaml")


def default_authority_source(doc_id: str, instant: datetime) -> bool:
    """True when the document is registered, unmodified and in force at this instant."""
    return is_authoritative(_corpus(), doc_id, instant)


def _fail(rule_id: str, *extra_citations: str, **fields: Any) -> PolicyFinding:
    item = rule(rule_id)
    return PolicyFinding(
        rule_id=rule_id,
        outcome=item.outcome_on_fail,
        category=item.category,
        owner=item.owner,
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


def _bank_change_unpaid(case: Case) -> bool:
    vendor = case.vendor
    if vendor.bank_changed_at is None:
        return False
    return vendor.last_paid_at is None or vendor.bank_changed_at > vendor.last_paid_at


def _high_risk_reasons(case: Case, clock: Clock) -> list[str]:
    vendor, invoice = case.vendor, case.invoice
    reasons = []
    age = (clock.as_of() - local_date(vendor.created_at)).days
    if age < rule("HIGH-RISK").threshold["vendor_age_days_lt"]:
        reasons.append("new vendor")
    if _bank_change_unpaid(case):
        reasons.append("changed bank account")
    overseas_remit = (
        invoice is not None and invoice.remit_to is not None and invoice.remit_to.country != HOME_COUNTRY
    )
    if "overseas_account" in vendor.risk_flags or overseas_remit:
        reasons.append("overseas account")
    if "manual_payment" in vendor.risk_flags:
        reasons.append("manual payment")
    if "fraud_flag" in vendor.risk_flags:
        reasons.append("fraud flag")
    return reasons


def _vendor_findings(case: Case, clock: Clock) -> list[PolicyFinding]:
    vendor, invoice = case.vendor, case.invoice
    if vendor is None:
        return [_unknown(rule_id, "vendor unavailable") for rule_id in VENDOR_RULES]
    citation = f"VENDOR:{vendor.vendor_id}"
    findings = []
    if vendor.status != VendorStatus.ACTIVE:
        findings.append(_fail("VENDOR-STATUS", citation, actual=str(vendor.status)))
    else:
        findings.append(_pass("VENDOR-STATUS", citation))
    threshold = rule("BANK-CHANGE-HOLD").threshold["business_days"]
    held = vendor.bank_changed_at is not None and clock.as_of() <= window_end(
        local_date(vendor.bank_changed_at), threshold
    )
    findings.append(_fail("BANK-CHANGE-HOLD", citation) if held else _pass("BANK-CHANGE-HOLD", citation))
    if invoice is None:
        findings.append(_unknown("REMIT-MATCHES-MASTER", "invoice unavailable"))
    elif invoice.remit_to is None or invoice.remit_to.fingerprint != vendor.fingerprint:
        findings.append(_fail("REMIT-MATCHES-MASTER", citation, detail="remit-to differs from vendor master"))
    else:
        findings.append(_pass("REMIT-MATCHES-MASTER", citation))
    first = _bank_change_unpaid(case)
    findings.append(
        _fail("FIRST-PAYMENT-AFTER-BANK-CHANGE", citation)
        if first
        else _pass("FIRST-PAYMENT-AFTER-BANK-CHANGE", citation)
    )
    reasons = _high_risk_reasons(case, clock)
    if reasons:
        findings.append(_fail("HIGH-RISK", citation, detail=", ".join(reasons)))
    elif invoice is None:
        # The remit-to country is unread, so "not overseas" cannot be claimed.
        findings.append(_unknown("HIGH-RISK", "invoice unavailable"))
    else:
        findings.append(_pass("HIGH-RISK", citation))
    return findings


def _posting_limits(case: Case) -> list[tuple[Decimal, str]]:
    """Register entries in a DFA posting role, each capped at the matrix limit for that role."""
    matrix = {role: Decimal(limit) for role, limit in rule("DFA-TIER").threshold.items()}
    return sorted(
        (min(item.limit, matrix[item.role]), item.role) for item in case.authority if item.role in matrix
    )


def _dfa_findings(
    case: Case, clock: Clock, commitment: Decimal, source: AuthoritySource
) -> tuple[list[PolicyFinding], str | None]:
    if not source(DFA_DOC, clock.now_utc()):
        return (
            [
                _fail("DFA-AUTHORITATIVE-SOURCE", detail="no authoritative DFA at as_of"),
                _unknown("DFA-TIER", "no authoritative DFA at as_of"),
            ],
            None,
        )
    findings = [_pass("DFA-AUTHORITATIVE-SOURCE")]
    if any(item.source == AUTHORITY_TOOL for item in case.unknowns):
        return [*findings, _unknown("DFA-TIER", "authority register unavailable")], None
    role = next((name for limit, name in _posting_limits(case) if limit >= commitment), None)
    if role is None:
        return [
            *findings,
            _fail("DFA-TIER", actual=commitment, detail="no posting role covers this amount"),
        ], None
    return [*findings, _pass("DFA-TIER")], role


def _segregation(case: Case, commitment: Decimal) -> PolicyFinding:
    if commitment <= Decimal(rule("SOD-ABOVE-25K").threshold["amount_gt"]):
        return _pass("SOD-ABOVE-25K")
    if case.po is None:
        return _unknown("SOD-ABOVE-25K", "requester and receipter unavailable")
    citation = f"PO:{case.po.po_ref}"
    if any(item.receipter == case.po.requester for item in case.po.receipts):
        return _fail("SOD-ABOVE-25K", citation, actual=commitment, detail="requester is also a receipter")
    return _pass("SOD-ABOVE-25K", citation)


def check(case: Case, clock: Clock, authority_source: AuthoritySource = default_authority_source) -> VAResult:
    findings = _vendor_findings(case, clock)
    invoice = case.invoice
    if invoice is None or invoice.currency is None or invoice.gross is None:
        reason = "invoice amount unavailable"
        unknown = [_unknown(rule_id, reason) for rule_id in (*AMOUNT_RULES, "FX-HOLD")]
        return VAResult((*findings, *unknown), None)
    po = case.po
    foreign = invoice.currency != HOME_CURRENCY or (po is not None and po.currency != invoice.currency)
    if foreign:
        # The AUD amount is unknown, so authority cannot be assessed (FIN-POL-009 s2).
        fx = _fail("FX-HOLD", expected=HOME_CURRENCY, actual=invoice.currency)
        segregation = _unknown("SOD-ABOVE-25K", "AUD amount unknown")
        return VAResult((*findings, fx, segregation), None)
    commitment = max(invoice.gross, po.totals.incl_tax) if po is not None else invoice.gross
    dfa, role = _dfa_findings(case, clock, commitment, authority_source)
    return VAResult((*findings, *dfa, _segregation(case, commitment), _pass("FX-HOLD")), role)
