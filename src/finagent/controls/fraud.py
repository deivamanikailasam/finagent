"""Fraud indicators (FIN-POL-005 s3, s4). Decision support only: evidence is exposed, nobody is labelled."""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from finagent.config.clock import local_date
from finagent.controls.rules import rule
from finagent.domain.case import Case
from finagent.domain.enums import ExceptionCategory, Outcome, TrustClass
from finagent.domain.results import PolicyFinding
from finagent.tools.schemas import RetrievalItem

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "fraud_indicators.yaml"
RULE_ID = "FRAUD-INDICATORS"
BANK_INDICATOR = "changed_bank_details"
NAME_INDICATOR = "vendor_name_mismatch"


@dataclass(frozen=True)
class Hit:
    indicator: str
    doc_id: str
    offset: int | None = None


@dataclass(frozen=True)
class FraudResult:
    indicators: tuple[Hit, ...]
    findings: tuple[PolicyFinding, ...]
    outcome: Outcome | None
    category: ExceptionCategory | None
    owner: str | None
    notify_supplier: bool
    security_events: tuple[dict[str, Any], ...]
    bank_detail_claim: bool


@dataclass(frozen=True)
class _Config:
    threshold: int
    bank_days: int
    text: dict[str, tuple[re.Pattern[str], ...]]
    claim: tuple[re.Pattern[str], ...]


@lru_cache(maxsize=1)
def _config() -> _Config:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    flags = re.DOTALL
    return _Config(
        threshold=raw["escalate_threshold"],
        bank_days=raw["bank_changed_within_days"],
        text={
            name: tuple(re.compile(pattern, flags) for pattern in patterns)
            for name, patterns in raw["text_indicators"].items()
        },
        claim=tuple(re.compile(pattern, flags) for pattern in raw["bank_detail_claim"]),
    )


def _normalise(text: str) -> str:
    kept = (char for char in unicodedata.normalize("NFKC", text) if unicodedata.category(char) != "Cf")
    return "".join(kept).casefold()


def _scan(text: str, doc_id: str) -> list[Hit]:
    normalised = _normalise(text)
    hits = []
    for indicator, patterns in _config().text.items():
        offsets = [match.start() for pattern in patterns if (match := pattern.search(normalised))]
        if offsets:
            hits.append(Hit(indicator, doc_id, min(offsets)))
    return hits


def _claims_bank_change(text: str) -> bool:
    normalised = _normalise(text)
    return any(pattern.search(normalised) for pattern in _config().claim)


def _is_linked(case: Case, chunk: RetrievalItem) -> bool:
    """Retrieved text counts only when it belongs to this case or names this vendor (decision log X)."""
    if chunk.doc_id.startswith("CASE:"):
        return True
    if case.vendor is None:
        return False
    text = _normalise(chunk.text)
    return _normalise(case.vendor.vendor_id) in text or _normalise(case.vendor.legal_name) in text


def _structural_hits(case: Case) -> list[Hit]:
    vendor, invoice = case.vendor, case.invoice
    if vendor is None or invoice is None:
        return []
    hits = []
    vendor_id = f"VENDOR:{vendor.vendor_id}"
    remit_differs = invoice.remit_to is not None and invoice.remit_to.fingerprint != vendor.fingerprint
    recent = False
    if vendor.bank_changed_at is not None:
        days = (local_date(case.as_of) - local_date(vendor.bank_changed_at)).days
        recent = days <= _config().bank_days
    if remit_differs or recent:
        hits.append(Hit(BANK_INDICATOR, vendor_id))
    if invoice.legal_name is not None:
        if " ".join(_normalise(invoice.legal_name).split()) != " ".join(
            _normalise(vendor.legal_name).split()
        ):
            hits.append(Hit(NAME_INDICATOR, vendor_id))
    return hits


def _finding(
    case: Case, hits: list[Hit], outcome: Outcome | None, category: ExceptionCategory | None
) -> PolicyFinding:
    item = rule(RULE_ID)
    names = sorted({hit.indicator for hit in hits})
    detail = f"{len(names)} indicator(s): {', '.join(names)}" if names else "no indicators"
    evidence = tuple(dict.fromkeys(hit.doc_id for hit in hits))
    if outcome is not None:
        return PolicyFinding(
            rule_id=RULE_ID,
            outcome=outcome,
            category=category,
            owner=item.owner,
            citations=(*item.citations, *evidence),
            detail=detail,
        )
    if case.vendor is None or case.invoice is None:
        return PolicyFinding(
            rule_id=RULE_ID,
            outcome="UNKNOWN",
            category=ExceptionCategory.OTHER_CONTROL_RISK,
            citations=item.citations,
            detail="structural indicators unavailable",
        )
    return PolicyFinding(
        rule_id=RULE_ID, outcome="PASS", citations=(*item.citations, *evidence), detail=detail
    )


def detect(case: Case, retrieved_chunks: tuple[RetrievalItem, ...] | list[RetrievalItem]) -> FraudResult:
    hits = _scan(case.request.notes, "REQUEST:notes") if case.request.notes else []
    claim = False
    seen: set[str] = set()
    for document in case.evidence.documents:
        if document.origin == "supplier":
            doc_id = f"CASE:{document.ref}"
            seen.add(doc_id)
            hits.extend(_scan(document.text, doc_id))
            claim = claim or _claims_bank_change(document.text)
    events: list[dict[str, Any]] = []
    for chunk in retrieved_chunks:
        # Policy text describes these indicators, so only untrusted text is scanned.
        if chunk.trust_class != TrustClass.EXTERNAL_UNVERIFIED or chunk.doc_id in seen:
            continue
        seen.add(chunk.doc_id)
        found = _scan(chunk.text, chunk.doc_id)
        if _is_linked(case, chunk):
            hits.extend(found)
            claim = claim or _claims_bank_change(chunk.text)
        elif found:
            names = sorted({hit.indicator for hit in found})
            events.append({"event": "unlinked_risk_text", "doc_id": chunk.doc_id, "indicators": names})
    hits.extend(_structural_hits(case))
    distinct = {hit.indicator for hit in hits}
    outcome = category = owner = None
    if len(distinct) >= _config().threshold:
        outcome = Outcome.ESCALATE_CONTROL_REVIEW
        choices = rule(RULE_ID).category
        category = ExceptionCategory(
            choices["if_bank_indicator"] if BANK_INDICATOR in distinct else choices["otherwise"]
        )
        owner = rule(RULE_ID).owner
    finding = _finding(case, hits, outcome, category)
    return FraudResult(
        indicators=tuple(hits),
        findings=(finding,),
        outcome=outcome,
        category=category,
        owner=owner,
        notify_supplier=False,  # FIN-POL-007 s3: suspicion is never shared with the supplier
        security_events=tuple(events),
        bank_detail_claim=claim,
    )
