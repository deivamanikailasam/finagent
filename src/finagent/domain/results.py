"""The typed final result (decision log I6) and its parts."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from finagent.config.clock import add_business_days
from finagent.domain.enums import ExceptionCategory, Outcome
from finagent.domain.types import DomainModel

REVIEW_BUSINESS_DAYS = 3  # FIN-POL-007 s4

Value = Decimal | int | bool | str | None
Source = Literal["TEMPLATE", "LLM"]


class Fact(DomainModel):
    id: str = Field(min_length=1)
    value: Value
    source_id: str = Field(min_length=1)


class Calculation(DomainModel):
    id: str = Field(min_length=1)
    inputs: dict[str, Value]
    formula: str = Field(min_length=1)
    result: Value
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
    citations: tuple[str, ...] = ()
    line_ref: str | None = None
    expected: Value = None
    actual: Value = None
    difference: Value = None
    threshold: Value = None
    detail: str | None = None


class ExceptionRecord(DomainModel):
    category: ExceptionCategory
    failed_rule: str = Field(min_length=1)
    line_ref: str | None = None
    expected: Value = None
    actual: Value = None
    difference: Value = None
    threshold: Value = None
    citations: tuple[str, ...] = Field(min_length=1)
    owner: str = Field(min_length=1)
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
    next_action: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    rationale_source: Source


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
