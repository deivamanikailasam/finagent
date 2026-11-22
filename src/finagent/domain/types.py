"""Shared strict types: exact money, identifiers and a base model that refuses floats."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

MAX_MONEY_DIGITS = 15

Currency = Literal["AUD", "USD", "NZD", "EUR", "GBP"]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _reject_inexact(value: Any) -> Any:
    if isinstance(value, float | bool):
        raise ValueError("money must be a Decimal, string or integer, never a float or bool")
    return value


def _check_money(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("money must be finite")
    _, digits, exponent = value.as_tuple()
    if exponent < -2:
        raise ValueError("money has more than 2 decimal places")
    if len(digits) + max(exponent, 0) > MAX_MONEY_DIGITS:
        raise ValueError(f"money has more than {MAX_MONEY_DIGITS} digits")
    return value


def _not_negative(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("money must not be negative here")
    return value


# Negative values are legal only on invoice lines, where NEGATIVE-LINE must be able to see them.
LineMoney = Annotated[Decimal, BeforeValidator(_reject_inexact), AfterValidator(_check_money)]
Money = Annotated[LineMoney, AfterValidator(_not_negative)]


def _contains_float(value: Any) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, Mapping):
        return any(_contains_float(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_float(item) for item in value)
    return False


class DomainModel(BaseModel):
    """Frozen, closed model. No field may carry a float unless the class names it in float_fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    float_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _refuse_floats(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            for key, value in data.items():
                if key not in cls.float_fields and _contains_float(value):
                    raise ValueError(f"float not allowed in {key}")
        return data
