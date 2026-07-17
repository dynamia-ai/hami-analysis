from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


UTC_PLUS_EIGHT_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True, slots=True)
class ScanPeriod:
    timezone: str
    local_start: datetime
    local_end: datetime
    utc_start: datetime
    utc_end: datetime

    @property
    def start_date(self) -> str:
        return self.local_start.date().isoformat()

    @property
    def end_date(self) -> str:
        return self.local_end.date().isoformat()

    @property
    def search_start_date(self) -> str:
        return self.local_start.date().isoformat()

    @property
    def search_end_date(self) -> str:
        return self.local_end.date().isoformat()

    def contains(self, value: datetime | None) -> bool:
        if value is None:
            return False
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        utc_value = value.astimezone(UTC)
        return self.utc_start <= utc_value <= self.utc_end


def build_scan_period(
    *,
    days: int,
    timezone: str,
    start_date: date | None = None,
    end_date: date | None = None,
    now: datetime | None = None,
) -> ScanPeriod:
    if timezone != UTC_PLUS_EIGHT_TIMEZONE:
        raise ValueError(f"timezone must be {UTC_PLUS_EIGHT_TIMEZONE} (UTC+8)")
    if (start_date is None) != (end_date is None):
        raise ValueError("--start-date and --end-date must be provided together")
    zone = ZoneInfo(timezone)
    current = now or datetime.now(zone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=zone)
    else:
        current = current.astimezone(zone)

    if start_date is not None and end_date is not None:
        if start_date > end_date:
            raise ValueError("start date must not be after end date")
        local_start = datetime.combine(start_date, time.min, tzinfo=zone)
        local_end = datetime.combine(end_date, time.max, tzinfo=zone)
    else:
        local_end = current
        first_day = current.date() - timedelta(days=days - 1)
        local_start = datetime.combine(first_day, time.min, tzinfo=zone)

    return ScanPeriod(
        timezone=timezone,
        local_start=local_start,
        local_end=local_end,
        utc_start=local_start.astimezone(UTC),
        utc_end=local_end.astimezone(UTC),
    )
