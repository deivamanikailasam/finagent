"""The decision table (decision log IV): typed findings in, one recommendation out."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from finagent.config.clock import Clock
from finagent.controls import calculator, duplicates, fraud, matching, vendor_authority
from finagent.controls.rules import load_rules
from finagent.domain.case import Case
from finagent.domain.enums import ExceptionCategory as Category
from finagent.domain.enums import Outcome
from finagent.domain.results import Calculation, ExceptionRecord, PolicyFinding, Unknown, next_review_date

OUTCOME_PRECEDENCE = (
    Outcome.ESCALATE_CONTROL_REVIEW,
    Outcome.REJECT_DUPLICATE,
    Outcome.REJECT_INVALID,
    Outcome.HOLD_FOR_INFORMATION,
    Outcome.APPROVE_FOR_POSTING,
)
CATEGORY_ORDER = (
    Category.VENDOR_BLOCK,
    Category.BANK_CHANGE,
    Category.DUPLICATE_RISK,
    Category.MISSING_PO,
    Category.MISSING_RECEIPT,
    Category.AUTHORITY_GAP,
    Category.QUANTITY_VARIANCE,
    Category.PRICE_VARIANCE,
    Category.TAX_QUERY,
    Category.OTHER_CONTROL_RISK,
)
NEXT_ACTION: dict[Category | None, str] = {
    Category.VENDOR_BLOCK: "Vendor Governance reviews status",
    Category.BANK_CHANGE: (
        "Open a Vendor Governance verification task using the contact already on the vendor master"
    ),
    Category.DUPLICATE_RISK: "AP Operations compares both records",
    Category.MISSING_PO: "Requester supplies an approved PO",
    Category.MISSING_RECEIPT: "Request confirmation from the designated receipter",
    Category.AUTHORITY_GAP: "Financial Control arranges the required approval",
    Category.QUANTITY_VARIANCE: "Requester and receipter reconcile",
    Category.PRICE_VARIANCE: "Requester confirms price via the PO owner",
    Category.TAX_QUERY: "AP Operations verifies tax with the supplier",
    Category.OTHER_CONTROL_RISK: "AP Operations reviews the finding",
    None: "Submit for approval under the DFA matrix",
}
FRAUD_ACTION = "Financial Crime and Controls to review the contributing evidence; supplier not notified"
VERIFY_BANK_ACTION = (
    "Vendor Governance to open a verification task using the contact already on the vendor master"
)
OUTCOME_CITATION = "FIN-POL-001#s3"
BLOCK_CITATION = "FIN-POL-007#s5"
DEFAULT_OWNER = "AP Operations"
PERSON_OWNERS = ("receipter", "requester")
UNKNOWN_PENALTY = Decimal("0.25")
ASSUMPTION_PENALTY = Decimal("0.05")
CONFIDENCE_FLOOR = Decimal("0.20")


@dataclass(frozen=True)
class Evaluation:
    outcome: Outcome
    primary_category: Category | None
    owner: str
    findings: tuple[PolicyFinding, ...]
    exceptions: tuple[ExceptionRecord, ...]
    calculations: tuple[Calculation, ...]
    unknowns: tuple[Unknown, ...]
    confidence: Decimal
    next_action: str
    dfa_role: str | None
    indicators: tuple[fraud.Hit, ...]
    security_events: tuple[dict[str, Any], ...]
    citations: tuple[str, ...]
    notify_supplier: bool


def _rank(finding: PolicyFinding, order: dict[str, int]) -> tuple[int, int, int]:
    outcome = Outcome(finding.outcome) if finding.outcome != "UNKNOWN" else Outcome.HOLD_FOR_INFORMATION
    return (
        OUTCOME_PRECEDENCE.index(outcome),
        CATEGORY_ORDER.index(finding.category),
        order.get(finding.rule_id, len(order)),  # first failing rule in decision log III order
    )


def _owner(finding: PolicyFinding, case: Case) -> str:
    rules = load_rules()
    owner = finding.owner or (rules[finding.rule_id].owner if finding.rule_id in rules else DEFAULT_OWNER)
    person = next((name for name in PERSON_OWNERS if name in owner), None)
    if person is None:
        return owner
    known = finding.owner_known and case.po is not None
    return person if known else DEFAULT_OWNER


def confidence_score(unknown_sources: int, assumption_failures: int) -> Decimal:
    """A heuristic, not a probability: 1.0 - 0.25 per unknown source - 0.05 per assumption-based failure."""
    score = Decimal("1") - UNKNOWN_PENALTY * unknown_sources - ASSUMPTION_PENALTY * assumption_failures
    return max(score, CONFIDENCE_FLOOR).quantize(Decimal("0.01"))


def _is_assumption(finding: PolicyFinding) -> bool:
    item = load_rules().get(finding.rule_id)
    return item is not None and "assumption" in item.owner_basis


def _exception(finding: PolicyFinding, case: Case, clock: Clock) -> ExceptionRecord:
    return ExceptionRecord(
        category=finding.category,
        failed_rule=finding.rule_id,
        line_ref=finding.line_ref,
        expected=finding.expected,
        actual=finding.actual,
        difference=finding.difference,
        threshold=finding.threshold,
        citations=finding.citations,
        owner=_owner(finding, case),
        next_review_date=next_review_date(clock.as_of()),
    )


def _blocked_invoice_finding() -> PolicyFinding:
    return PolicyFinding(
        rule_id="BLOCKED-INVOICE",
        outcome=Outcome.HOLD_FOR_INFORMATION,
        category=Category.OTHER_CONTROL_RISK,
        owner=DEFAULT_OWNER,
        citations=(BLOCK_CITATION,),
        detail="the invoice has a payment block, which has no release path",
    )


def evaluate(
    case: Case,
    clock: Clock,
    authority_source: vendor_authority.AuthoritySource = vendor_authority.default_authority_source,
    invoice_blocked: bool = False,
) -> Evaluation:
    calc = calculator.calculate(case)
    authority = vendor_authority.check(case, clock, authority_source)
    indicators = fraud.detect(case, case.retrieved_chunks)
    findings = [
        *matching.check(case, calc, clock),
        *duplicates.check(case, clock),
        *authority.findings,
        *indicators.findings,
    ]
    failing = [item for item in findings if item.outcome not in {"PASS", "UNKNOWN"}]
    if invoice_blocked and not failing:
        blocked = _blocked_invoice_finding()
        findings.append(blocked)
        failing.append(blocked)
    order = {rule_id: index for index, rule_id in enumerate(load_rules())}
    # Missing evidence holds the case: with no failure, the unknown checks become the exceptions.
    open_items = failing or [item for item in findings if item.outcome == "UNKNOWN"]
    open_items = sorted(open_items, key=lambda item: _rank(item, order))
    if open_items:
        top = open_items[0]
        outcome = OUTCOME_PRECEDENCE[_rank(top, order)[0]]
        category, owner, action = top.category, _owner(top, case), NEXT_ACTION[top.category]
    else:
        outcome, category, owner, action = Outcome.APPROVE_FOR_POSTING, None, DEFAULT_OWNER, NEXT_ACTION[None]
    if indicators.outcome is not None:
        owner, action = indicators.owner, FRAUD_ACTION  # FIN-POL-007 s3, whatever the primary category
    if indicators.bank_detail_claim:
        action = f"{action}; {VERIFY_BANK_ACTION}"
    unknown_sources = len({item.source for item in case.unknowns})
    citations = dict.fromkeys((OUTCOME_CITATION, *(c for item in findings for c in item.citations)))
    return Evaluation(
        outcome=outcome,
        primary_category=category,
        owner=owner,
        findings=tuple(findings),
        exceptions=tuple(_exception(item, case, clock) for item in open_items),
        calculations=calc.records,
        unknowns=case.unknowns,
        confidence=confidence_score(unknown_sources, sum(map(_is_assumption, failing))),
        next_action=action,
        dfa_role=authority.dfa_role,
        indicators=indicators.indicators,
        security_events=indicators.security_events,
        citations=tuple(citations),
        notify_supplier=indicators.notify_supplier,
    )
