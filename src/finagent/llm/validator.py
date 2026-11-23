"""Checks a model rationale before it may be shown. It reports errors and never changes a decision."""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

import yaml

from finagent.domain.enums import Outcome
from finagent.llm.client import Claim, Rationale
from finagent.llm.prompt import RECORD_CITATION, RationaleInput, number_values
from finagent.obs.redaction import redact

FORBIDDEN_CLAIMS_PATH = Path(__file__).resolve().parents[3] / "config" / "forbidden_claims.yaml"
_DATE = re.compile(r"(?<![0-9])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9])")
# Only these reference shapes are not amounts. Anything else containing a digit must be bound.
_REFERENCE = re.compile(
    r"\b(?:FIN-POL-[0-9]{3}(?:-OLD)?|FIN-[0-9]{3}[a-z]?|PO-L[0-9]+|PO-[0-9]+|INV-[0-9]+|V-[0-9]+|H-[0-9]+)\b"
    r"|#s[0-9]+|§\s*[0-9]+|\bs[0-9]+\b|\bsections?\s+[0-9]+\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?|\.[0-9]+")
_NO_EXCEPTIONS = re.compile(
    r"\bno\s+(?:exceptions?|issues?|problems?|concerns?)\b|\bexceptions?\s+(?:do\s+not|don't)\s+apply\b"
)
_OUTCOME_PHRASES = tuple(str(item).replace("_", " ").casefold() for item in Outcome)


@dataclass(frozen=True)
class ValidationError:
    code: str
    claim: int | None = None
    detail: str | None = None  # a trusted ID the repair needs, never model text

    def __str__(self) -> str:
        where = f"claim {self.claim}: " if self.claim is not None else ""
        return f"{where}{self.code}" + (f" ({self.detail})" if self.detail else "")


@lru_cache(maxsize=1)
def _forbidden() -> tuple[tuple[re.Pattern[str], ...], tuple[str, ...], tuple[re.Pattern[str], ...]]:
    spec = yaml.safe_load(FORBIDDEN_CLAIMS_PATH.read_text(encoding="utf-8"))
    patterns = tuple(re.compile(item["regex"]) for item in spec["patterns"])
    allowed = tuple(item.casefold() for item in spec["allowed_contexts"])
    return patterns, allowed, tuple(re.compile(item) for item in spec.get("allowed_patterns", ()))


def _normalise(text: str) -> str:
    """Compatibility forms, accents and invisible characters cannot hide a number or a forbidden word."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) not in {"Cf", "Mn"})


def _mixes_scripts(text: str) -> bool:
    """A word mixing Latin with Cyrillic or Greek letters is a look-alike, never ordinary wording."""
    for word in re.findall(r"\w+", text):
        names = [unicodedata.name(ch, "") for ch in word if ch.isalpha()]
        if any(n.startswith("LATIN") for n in names) and any(
            n.startswith(("CYRILLIC", "GREEK")) for n in names
        ):
            return True
    return False


def has_forbidden_claim(text: str) -> bool:
    patterns, allowed, allowed_patterns = _forbidden()
    normal = _normalise(text)
    if _mixes_scripts(normal):
        return True
    checked = " ".join(normal.casefold().split())
    for phrase in allowed:
        checked = checked.replace(phrase, " ")
    for pattern in allowed_patterns:
        checked = pattern.sub(" ", checked)
    joined = re.sub(r"(?<=[a-z])-(?=[a-z])", "", checked)  # "ap-proved" reads as "approved"
    return any(pattern.search(view) for pattern in patterns for view in (checked, joined))


def _as_decimal(text: str) -> Decimal | None:
    try:
        return abs(Decimal(text.replace(",", "")))
    except InvalidOperation:
        return None


def _numbers_are_bound(claim: Claim, values: dict[str, str]) -> bool:
    if any(ref not in values for ref in claim.number_refs):
        return False
    text = _normalise(claim.text)
    if any(unicodedata.category(ch) == "Nd" and not ch.isascii() for ch in text):
        return False  # digits from another script are not checked as amounts, so they are refused
    bound = [values[ref] for ref in claim.number_refs]
    text = _REFERENCE.sub(" ", text)
    for found in _DATE.findall(text):
        if found not in bound:
            return False
    amounts = {amount for amount in map(_as_decimal, bound) if amount is not None}
    return all(_as_decimal(found.rstrip(",")) in amounts for found in _NUMBER.findall(_DATE.sub(" ", text)))


def _whole_rationale_errors(rationale: Rationale, evaluation: RationaleInput) -> list[ValidationError]:
    """The explanation must state the fixed decision and leave out no exception or unknown."""
    text = " ".join(_normalise(claim.text).casefold().replace("_", " ") for claim in rationale.claims)
    fixed = str(evaluation.outcome).replace("_", " ").casefold()
    errors: list[ValidationError] = []
    if any(phrase in text for phrase in _OUTCOME_PHRASES if phrase != fixed):
        errors.append(ValidationError("outcome_contradiction"))
    if evaluation.exceptions and _NO_EXCEPTIONS.search(text):
        errors.append(ValidationError("outcome_contradiction"))
    for item in evaluation.exceptions:
        names = (
            item.failed_rule.casefold().replace("_", " "),
            str(item.category).replace("_", " ").casefold(),
        )
        if not any(name in text for name in names):
            errors.append(ValidationError("exception_omitted", detail=f"{item.failed_rule} {item.category}"))
    for item in evaluation.unknowns:
        source = item.source.casefold().replace("_", " ")
        short = re.sub(r"^(?:get|check)\s+", "", source)
        if source not in text and short not in text:
            errors.append(ValidationError("unknown_omitted", detail=item.source))
    return errors


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
        if has_forbidden_claim(claim.text):
            errors.append(ValidationError("forbidden_claim", index))
    return [*errors, *_whole_rationale_errors(rationale, evaluation)]


def store_raw(path: Path, raw_text: str) -> None:
    """Keep a model reply for audit, with sensitive data masked before it reaches disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(raw_text), encoding="utf-8")
