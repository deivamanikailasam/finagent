"""Injected time source and business-day arithmetic on the Australia/Sydney local date."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

SYDNEY = ZoneInfo("Australia/Sydney")
SAFETY_MARGIN_BUSINESS_DAYS = 1

# Extension point for a public-holiday calendar. Unused: the safety margin stands in for it.
holiday_calendar: Callable[[date], bool] | None = None


class Clock(Protocol):
    def now_utc(self) -> datetime: ...

    def as_of(self) -> date: ...


def _require_aware(instant: datetime) -> None:
    if instant.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")


def local_date(instant: datetime) -> date:
    _require_aware(instant)  # a naive value could land on the wrong control date
    return instant.astimezone(SYDNEY).date()


class FixedClock:
    def __init__(self, as_of: datetime) -> None:
        _require_aware(as_of)
        self._instant = as_of.astimezone(UTC)

    def now_utc(self) -> datetime:
        return self._instant

    def as_of(self) -> date:
        return local_date(self._instant)


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def as_of(self) -> date:
        return local_date(self.now_utc())


def _is_business_day(day: date) -> bool:
    return day.weekday() < 5


def add_business_days(start: date, n: int) -> date:
    """Move n Monday-to-Friday days from start. The start day itself is not counted."""
    step = timedelta(days=1 if n >= 0 else -1)
    day = start
    remaining = abs(n)
    while remaining:
        day += step
        if _is_business_day(day):
            remaining -= 1
    return day


def business_days_between(a: date, b: date) -> int:
    """Count business days after a, up to and including b. Negative when b is before a."""
    if b < a:
        return -business_days_between(b, a)
    return sum(_is_business_day(a + timedelta(days=offset)) for offset in range(1, (b - a).days + 1))


def window_end(start_date: date, n: int) -> date:
    return add_business_days(start_date, n + SAFETY_MARGIN_BUSINESS_DAYS)
