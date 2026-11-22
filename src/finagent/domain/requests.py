"""The untrusted processing request, bounded per decision log VIII."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, field_validator

from finagent.domain.enums import ReasonCode
from finagent.domain.types import Currency, DomainModel, LineMoney, Sha256Hex

CaseId = Annotated[str, Field(pattern=r"^[A-Z]{3}-[0-9]{3}[a-z]?$")]


class RequestAttachment(DomainModel):
    ref: str = Field(min_length=1)
    sha256: Sha256Hex
    origin: Literal["supplier", "internal"]


class ProcessingRequest(DomainModel):
    case_id: CaseId
    invoice_ref: str = Field(min_length=1)
    vendor_id: str = Field(min_length=1)
    amount: LineMoney
    currency: Currency
    notes: str = Field(default="", max_length=4000)
    attachments: tuple[RequestAttachment, ...] = Field(default=(), max_length=10)

    @field_validator("amount")
    @classmethod
    def _amount_is_positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            # Credit notes and refunds are out of scope (FIN-POL-008 s1), so this is an input error.
            raise ValueError(f"{ReasonCode.UNSUPPORTED_DOCUMENT_TYPE}: amount must be greater than zero")
        return value
