"""Checks a model rationale before it may be shown. It reports errors and never changes a decision."""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

import yaml

from finagent.llm.client import Claim, Rationale
from finagent.llm.prompt import RECORD_CITATION, RationaleInput, number_values
from finagent.obs.redaction import redact

FORBIDDEN_CLAIMS_PATH = Path(__file__).resolve().parents[3] / "config" / "forbidden_claims.yaml"
_DATE = re.compile(r"(?<![0-9A-Za-z])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9])")
# Digits inside an identifier (FIN-POL-003, PO-4471, s2) are not numbers; "9.9k" and "-50" still are.
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])(?<![A-Za-z0-9]-)"
    r"([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)"
    r"(?![0-9_]|-[A-Za-z0-9])"
)
_CURRENCY_PREFIX = re.compile(r"\b(AUD|USD|NZD|EUR|GBP)(?=[0-9])", re.IGNORECASE)


@dataclass(frozen=True)
class ValidationError:
    code: str
    claim: int | None = None

    def __str__(self) -> str:
        return f"claim {self.claim}: {self.code}" if self.claim is not None else self.code


@lru_cache(maxsize=1)
def _forbidden() -> tuple[tuple[re.Pattern[str], ...], tuple[str, ...]]:
    spec = yaml.safe_load(FORBIDDEN_CLAIMS_PATH.read_text(encoding="utf-8"))
    patterns = tuple(re.compile(item["regex"]) for item in spec["patterns"])
    return patterns, tuple(item.casefold() for item in spec["allowed_contexts"])


def _normalise(text: str) -> str:
    """Compatibility forms and invisible characters cannot hide a number or a forbidden word."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if unicodedata.category(ch) != "Cf")


def _has_forbidden_claim(text: str) -> bool:
    patterns, allowed = _forbidden()
    checked = " ".join(_normalise(text).casefold().split())
    for phrase in allowed:
        checked = checked.replace(phrase, " ")
    return any(pattern.search(checked) for pattern in patterns)


def _as_decimal(text: str) -> Decimal | None:
    try:
        return abs(Decimal(text.replace(",", "")))
    except InvalidOperation:
        return None


def _numbers_are_bound(claim: Claim, values: dict[str, str]) -> bool:
    if any(ref not in values for ref in claim.number_refs):
        return False
    bound = [values[ref] for ref in claim.number_refs]
    text = _CURRENCY_PREFIX.sub(r"\1 ", _normalise(claim.text))
    for found in _DATE.findall(text):
        if found not in bound:
            return False
    amounts = {amount for amount in map(_as_decimal, bound) if amount is not None}
    return all(_as_decimal(found) in amounts for found in _NUMBER.findall(_DATE.sub(" ", text)))


def _citations_support(claim: Claim, evaluation: RationaleInput) -> bool:
    """A claim naming a finding must cite that finding's source, and no other policy section."""
    named = [
        finding
        for finding in evaluation.findings
        if re.search(rf"(?<![A-Za-z0-9-]){re.escape(finding.rule_id)}(?![A-Za-z0-9-])", claim.text)
    ]
    if not named:
        return True
    support = {citation for finding in named for citation in finding.citations}
    cited = set(claim.citation_ids)
    policy = {item for item in cited if not item.startswith(RECORD_CITATION)}
    return bool(cited & support) and policy <= support


def validate(
    rationale: Rationale, evaluation: RationaleInput, allowed: Iterable[str] | None = None
) -> list[ValidationError]:
    permitted = set(evaluation.citations if allowed is None else allowed)
    values = number_values(evaluation)
    errors: list[ValidationError] = []
    for index, claim in enumerate(rationale.claims, 1):
        if any(citation not in permitted for citation in claim.citation_ids):
            errors.append(ValidationError("unknown_citation", index))
        if not _citations_support(claim, evaluation):
            errors.append(ValidationError("citation_support", index))
        if not _numbers_are_bound(claim, values):
            errors.append(ValidationError("number_binding", index))
        if _has_forbidden_claim(claim.text):
            errors.append(ValidationError("forbidden_claim", index))
    return errors


def store_raw(path: Path, raw_text: str) -> None:
    """Keep a model reply for audit, with sensitive data masked before it reaches disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(raw_text), encoding="utf-8")
