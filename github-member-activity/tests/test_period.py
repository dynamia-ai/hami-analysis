from datetime import UTC, datetime

import pytest

from github_member_activity.period import _local_midnight, build_period, effective_window, parse_rfc3339
from zoneinfo import ZoneInfo


def test_previous_week_is_half_open():
    value = build_period("weekly", "Asia/Shanghai", now=datetime(2026, 8, 1, 12, tzinfo=UTC))
    assert value.start_local.isoformat() == "2026-07-20T00:00:00+08:00"
    assert value.end_local.isoformat() == "2026-07-27T00:00:00+08:00"
    assert value.contains(parse_rfc3339("2026-07-19T16:00:00Z"))
    assert not value.contains(parse_rfc3339("2026-07-26T16:00:00Z"))


def test_explicit_fractional_and_long_periods_rejected():
    with pytest.raises(ValueError):
        parse_rfc3339("2026-01-01T00:00:00.1Z")
    with pytest.raises(ValueError):
        build_period("explicit", "UTC", start="2026-01-01T00:00:00Z", end="2027-01-02T00:00:00Z")
    with pytest.raises(ValueError):
        build_period("explicit", "UTC", start="2026-01-01 00:00:00Z", end="2026-01-02T00:00:00Z")


def test_active_until_is_exclusive():
    period = build_period("explicit", "UTC", start="2026-01-01T00:00:00Z", end="2026-02-01T00:00:00Z")
    assert effective_window(period, __import__("datetime").date(2026, 1, 10), __import__("datetime").date(2026, 1, 20))[1].date().isoformat() == "2026-01-20"


def test_nonexistent_local_midnight_is_rejected():
    with pytest.raises(ValueError):
        _local_midnight(__import__("datetime").date(2011, 12, 30), ZoneInfo("Pacific/Apia"))
