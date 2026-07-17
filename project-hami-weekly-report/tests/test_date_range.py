from datetime import UTC, date, datetime

import pytest

from hami_github_activity.date_range import build_scan_period


def test_default_period_is_seven_calendar_days_including_today() -> None:
    period = build_scan_period(
        days=7,
        timezone="Asia/Shanghai",
        now=datetime(2026, 7, 16, 14, 30, tzinfo=UTC),
    )
    assert period.local_start.isoformat() == "2026-07-10T00:00:00+08:00"
    assert period.local_end.isoformat() == "2026-07-16T22:30:00+08:00"


def test_timezone_conversion_to_utc() -> None:
    period = build_scan_period(
        days=7,
        timezone="Asia/Shanghai",
        now=datetime(2026, 7, 16, 14, 30, tzinfo=UTC),
    )
    assert period.utc_start.isoformat() == "2026-07-09T16:00:00+00:00"
    assert period.utc_end.isoformat() == "2026-07-16T14:30:00+00:00"
    assert period.search_start_date == "2026-07-10"
    assert period.search_end_date == "2026-07-16"


def test_explicit_dates_cover_complete_local_days() -> None:
    period = build_scan_period(
        days=7,
        timezone="Asia/Shanghai",
        start_date=date(2026, 7, 10),
        end_date=date(2026, 7, 16),
    )
    assert period.local_start.isoformat() == "2026-07-10T00:00:00+08:00"
    assert period.local_end.isoformat() == "2026-07-16T23:59:59.999999+08:00"
    assert period.contains(datetime(2026, 7, 16, 15, 59, 59, 999999, tzinfo=UTC))


def test_explicit_dates_must_be_paired_and_ordered() -> None:
    with pytest.raises(ValueError, match="provided together"):
        build_scan_period(days=7, timezone="Asia/Shanghai", start_date=date(2026, 7, 10))
    with pytest.raises(ValueError, match="must not be after"):
        build_scan_period(
            days=7,
            timezone="Asia/Shanghai",
            start_date=date(2026, 7, 16),
            end_date=date(2026, 7, 10),
        )


def test_scan_period_requires_utc_plus_eight() -> None:
    with pytest.raises(ValueError, match="Asia/Shanghai"):
        build_scan_period(days=7, timezone="UTC")
