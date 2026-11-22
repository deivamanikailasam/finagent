"""One evidence query per check. Queries use fixed policy vocabulary and typed fields only."""

from collections.abc import Callable

from finagent.domain.case import Case


def _currency(case: Case) -> str:
    return str(case.request.currency)


def _vendor_status(case: Case) -> str:
    return str(case.vendor.status) if case.vendor is not None else "unknown"


TEMPLATES: dict[str, Callable[[Case], str]] = {
    "invoice_evidence": lambda case: (
        f"minimum evidence invoice legal name number date {_currency(case)} "
        "gross amount tax purchase-order reference"
    ),
    "po_matching": lambda case: (
        "three-way matching approved purchase order per line document total tolerance variance"
    ),
    "receipt_evidence": lambda case: (
        "missing receipt goods service receipt confirmation designated receipter"
    ),
    "duplicate_policy": lambda case: (
        "duplicate detection exact match normalised invoice number paid posted held rejected"
    ),
    "vendor_status": lambda case: (
        f"vendor status {_vendor_status(case)} held blocked dormant sanctions review pending verification"
    ),
    "bank_change": lambda case: "bank account change verified vendor master payment instructions hold",
    "dfa_tier": lambda case: (
        f"delegated financial authority maximum approval role limit {_currency(case)} total commitment"
    ),
    "high_risk": lambda case: (
        "higher-risk transactions new vendor changed bank account overseas manual payment two approvals"
    ),
    "segregation": lambda case: "segregation of duties requester receipter financial approver distinct",
    "fx": lambda case: (
        f"foreign currency invoice {_currency(case)} purchase order currency conversion variance"
    ),
    "fraud_indicators": lambda case: (
        "fraud indicators urgent secret payment language bypass approval escalate"
    ),
    "exception_routing": lambda case: "exception categories required record escalation targets owner review",
}

EXPECTED_SOURCE: dict[str, list[str]] = {
    "invoice_evidence": ["FIN-POL-001#s2", "FIN-POL-001#s5", "FIN-POL-002#s1", "FIN-POL-008#s1"],
    "po_matching": ["FIN-POL-001#s2", "FIN-POL-002#s1", "FIN-POL-002#s2", "FIN-POL-002#s3"],
    "receipt_evidence": ["FIN-POL-002#s1", "FIN-POL-002#s2", "FIN-POL-002#s4"],
    "duplicate_policy": ["FIN-POL-005#s1", "FIN-POL-005#s2"],
    "vendor_status": ["FIN-POL-004#s4"],
    "bank_change": ["FIN-POL-001#s5", "FIN-POL-004#s2"],
    "dfa_tier": ["FIN-POL-003#s2"],
    "high_risk": ["FIN-POL-003#s3"],
    "segregation": ["FIN-POL-001#s4"],
    "fx": ["FIN-POL-009#s1", "FIN-POL-009#s3"],
    "fraud_indicators": ["FIN-POL-005#s3", "FIN-POL-005#s4"],
    "exception_routing": ["FIN-POL-007#s1", "FIN-POL-007#s2", "FIN-POL-007#s3"],
}
