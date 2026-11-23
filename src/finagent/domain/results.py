"""The typed final result (decision log I6) and its parts."""

import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BeforeValidator, Field, PlainSerializer, model_validator

from finagent.config.clock import add_business_days
from finagent.domain.enums import ExceptionCategory, Outcome
from finagent.domain.types import DomainModel, ExactDecimal, NonBlank

REVIEW_BUSINESS_DAYS = 3  # FIN-POL-007 s4
_DECIMAL_TAG = "decimal"
_DECIMAL_TEXT = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


def _decode_value(value: Any) -> Any:
    if isinstance(value, Mapping) and set(value) == {_DECIMAL_TAG}:
        text = value[_DECIMAL_TAG]
        if not isinstance(text, str) or not _DECIMAL_TEXT.fullmatch(text):
            raise ValueError("invalid decimal value")
        return Decimal(text)
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("value must be finite")
    return value


def _encode_value(value: Any) -> Any:
    return {_DECIMAL_TAG: str(value)} if isinstance(value, Decimal) else value


# A tagged JSON form, so a Decimal is still a Decimal after a checkpoint is reloaded.
Value = Annotated[
    Decimal | int | bool | str | None,
    BeforeValidator(_decode_value),
    PlainSerializer(_encode_value, when_used="json"),
]
Source = Literal["TEMPLATE", "LLM"]


class Fact(DomainModel):
    id: str = Field(min_length=1)
    value: Value
    source_id: str = Field(min_length=1)


class Calculation(DomainModel):
    id: str = Field(min_length=1)
    inputs: dict[str, ExactDecimal]
    formula: str = Field(min_length=1)
    result: ExactDecimal
    rounding_method: str = Field(min_length=1)


class Inference(DomainModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    basis_ids: tuple[str, ...] = Field(min_length=1)
    source: Source


class Assumption(DomainModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)


class Unknown(DomainModel):
    source: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class PolicyFinding(DomainModel):
    rule_id: str = Field(min_length=1)
    # PASS and UNKNOWN sit beside the outcomes: an unknown PO rule must not read as a failure (FIN-004).
    outcome: Outcome | Literal["PASS", "UNKNOWN"]
    category: ExceptionCategory | None = None
    owner: str | None = None
    owner_known: bool = True
    citations: tuple[NonBlank, ...] = ()
    line_ref: str | None = None
    expected: Value = None
    actual: Value = None
    difference: Value = None
    threshold: Value = None
    detail: str | None = None


class ExceptionRecord(DomainModel):
    category: ExceptionCategory
    failed_rule: NonBlank
    line_ref: str | None = None
    expected: Value = None
    actual: Value = None
    difference: Value = None
    threshold: Value = None
    citations: tuple[NonBlank, ...] = Field(min_length=1)
    owner: NonBlank
    next_review_date: date


class Action(DomainModel):
    id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    action: str = Field(min_length=1)
    authorisation_id: str | None = None
    idempotency_key: str | None = None
    posting_ref: str | None = None
    at: AwareDatetime


class Recommendation(DomainModel):
    outcome: Outcome
    primary_category: ExceptionCategory | None = None
    confidence: Annotated[Decimal, Field(ge=0, le=1)]
    next_action: NonBlank
    owner: NonBlank
    rationale_source: Source

    @model_validator(mode="after")
    def _category_matches_outcome(self) -> "Recommendation":
        # Only a clean approval has no exception category (FIN-POL-007 s1).
        if (self.outcome == Outcome.APPROVE_FOR_POSTING) != (self.primary_category is None):
            raise ValueError("an approval has no category and every other outcome needs one")
        return self


class FinalResult(DomainModel):
    facts: tuple[Fact, ...] = ()
    calculations: tuple[Calculation, ...] = ()
    inferences: tuple[Inference, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    unknowns: tuple[Unknown, ...] = ()
    findings: tuple[PolicyFinding, ...] = ()
    exceptions: tuple[ExceptionRecord, ...] = ()
    actions_taken: tuple[Action, ...] = ()
    recommendation: Recommendation


def next_review_date(as_of_date: date) -> date:
    return add_business_days(as_of_date, REVIEW_BUSINESS_DAYS)
