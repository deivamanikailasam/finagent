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
_LINE_LABEL = re.compile(r"\blines?\s+([0-9]+)\b", re.IGNORECASE)
_NUMBER = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?|\.[0-9]+")
_NO_EXCEPTIONS = re.compile(
    r"\bno\s+(?:exceptions?|issues?|problems?|concerns?)\b|\bexceptions?\s+(?:do\s+not|don't)\s+apply\b"
)
_OUTCOME_PHRASES = tuple(str(item).replace("_", " ").casefold() for item in Outcome)


@dataclass(frozen=True)
class ValidationError:
    code: str
    claim: int | None = None
    detail: str | None = None  # a trusted ID or a matched numeral for the repair, never free model text

    def __str__(self) -> str:
        where = f"claim {self.claim}: " if self.claim is not None else ""
        return f"{where}{self.code}" + (f" ({self.detail})" if self.detail else "")


@lru_cache(maxsize=1)
def _forbidden() -> tuple[
    tuple[tuple[str, re.Pattern[str]], ...], tuple[str, ...], tuple[re.Pattern[str], ...]
]:
    spec = yaml.safe_load(FORBIDDEN_CLAIMS_PATH.read_text(encoding="utf-8"))
    patterns = tuple((item["intent"], re.compile(item["regex"])) for item in spec["patterns"])
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


def forbidden_intent(text: str) -> str | None:
    """The configured intent a claim breaks, so a repair is told the rule and never echoes the text."""
    patterns, allowed, allowed_patterns = _forbidden()
    normal = _normalise(text)
    if _mixes_scripts(normal):
        return "look_alike_characters"
    checked = " ".join(normal.casefold().split())
    for phrase in allowed:
        checked = checked.replace(phrase, " ")
    for pattern in allowed_patterns:
        checked = pattern.sub(" ", checked)
    joined = re.sub(r"(?<=[a-z])-(?=[a-z])", "", checked)  # "ap-proved" reads as "approved"
    for intent, pattern in patterns:
        if pattern.search(checked) or pattern.search(joined):
            return intent
    return None


def has_forbidden_claim(text: str) -> bool:
    return forbidden_intent(text) is not None


def _as_decimal(text: str) -> Decimal | None:
    try:
        return abs(Decimal(text.replace(",", "")))
    except InvalidOperation:
        return None


def _binding_problem(claim: Claim, values: dict[str, str], line_count: int) -> str | None:
    """Why a claim's numbers are not bound. The text is fixed wording or a matched numeral only."""
    if any(ref not in values for ref in claim.number_refs):
        return "number_refs holds an unknown ID"
    text = _normalise(claim.text)
    if any(unicodedata.category(ch) == "Nd" and not ch.isascii() for ch in text):
        return "digits must be ASCII"  # digits from another script are not checked as amounts
    bound = [values[ref] for ref in claim.number_refs]
    text = _REFERENCE.sub(" ", text)
    # "line 2" is a label only when the invoice has a line 2; any other number must still be bound.
    text = _LINE_LABEL.sub(lambda m: " " if 1 <= int(m.group(1)) <= line_count else m.group(0), text)
    for found in _DATE.findall(text):
        if found not in bound:
            return f"unbound date {found}"
    amounts = {amount for amount in map(_as_decimal, bound) if amount is not None}
    for found in _NUMBER.findall(_DATE.sub(" ", text)):
        if _as_decimal(found.rstrip(",")) not in amounts:
            return f"unbound number {found.rstrip(',')}"
    return None


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
        binding_problem = _binding_problem(claim, values, len(evaluation.lines))
        if binding_problem is not None:
            errors.append(ValidationError("number_binding", index, binding_problem))
        intent = forbidden_intent(claim.text)
        if intent is not None:
            errors.append(ValidationError("forbidden_claim", index, intent))
    return [*errors, *_whole_rationale_errors(rationale, evaluation)]


def store_raw(path: Path, raw_text: str) -> None:
    """Keep a model reply for audit, with sensitive data masked before it reaches disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(raw_text), encoding="utf-8")
