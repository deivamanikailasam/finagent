from datetime import UTC, date, datetime

import pytest

from finagent.config.clock import (
    SAFETY_MARGIN_BUSINESS_DAYS,
    SYDNEY,
    FixedClock,
    SystemClock,
    add_business_days,
    business_days_between,
    local_date,
    window_end,
)


def test_sydney_date_crosses_midnight_before_utc():
    assert local_date(datetime(2026, 7, 31, 15, 0, tzinfo=UTC)) == date(2026, 8, 1)


def test_sydney_date_uses_daylight_saving_offset():
    assert local_date(datetime(2026, 12, 31, 13, 0, tzinfo=UTC)) == date(2027, 1, 1)
    assert local_date(datetime(2026, 6, 30, 13, 0, tzinfo=UTC)) == date(2026, 6, 30)


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        local_date(datetime(2026, 9, 15, 9, 0))
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 9, 15, 9, 0))


def test_fixed_clock_reports_utc_and_sydney_date():
    clock = FixedClock(datetime(2026, 9, 15, 9, 0, tzinfo=SYDNEY))
    assert clock.now_utc() == datetime(2026, 9, 14, 23, 0, tzinfo=UTC)
    assert clock.as_of() == date(2026, 9, 15)


def test_system_clock_is_utc_aware():
    assert SystemClock().now_utc().utcoffset().total_seconds() == 0


def test_add_business_days_skips_weekend():
    friday = date(2026, 9, 11)
    assert add_business_days(friday, 1) == date(2026, 9, 14)
    assert add_business_days(date(2026, 9, 14), -1) == friday
    assert add_business_days(friday, 0) == friday


def test_add_business_days_from_a_weekend():
    assert add_business_days(date(2026, 9, 12), 1) == date(2026, 9, 14)


def test_business_days_between_across_a_weekend():
    assert business_days_between(date(2026, 9, 11), date(2026, 9, 14)) == 1
    assert business_days_between(date(2026, 9, 14), date(2026, 9, 11)) == -1
    assert business_days_between(date(2026, 9, 11), date(2026, 9, 11)) == 0
    assert business_days_between(date(2026, 9, 12), date(2026, 9, 13)) == 0


def test_window_end_includes_the_margin():
    assert SAFETY_MARGIN_BUSINESS_DAYS == 1
    assert window_end(date(2026, 9, 10), 2) == date(2026, 9, 15)
    assert window_end(date(2026, 9, 9), 2) == date(2026, 9, 14)
