from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import re

RFC3339_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")


@dataclass(frozen=True, slots=True)
class ReportPeriod:
    kind: str
    timezone: str
    start_local: datetime
    end_local: datetime

    @property
    def start_utc(self) -> datetime:
        return self.start_local.astimezone(UTC)

    @property
    def end_utc(self) -> datetime:
        return self.end_local.astimezone(UTC)

    @property
    def id(self) -> str:
        if self.kind == "explicit":
            return f"explicit-{basic_utc(self.start_utc)}--{basic_utc(self.end_utc)}"
        return f"{self.kind}-{self.start_local:%Y%m%d}--{self.end_local:%Y%m%d}"

    def contains(self, value: datetime) -> bool:
        value = ensure_utc(value)
        return self.start_utc <= value < self.end_utc

    def to_json(self) -> dict[str, str]:
        return {
            "id": self.id,
            "timezone": self.timezone,
            "start_local": self.start_local.isoformat(),
            "end_local": self.end_local.isoformat(),
            "start_utc": format_z(self.start_utc),
            "end_utc": format_z(self.end_utc),
        }


def format_z(value: datetime) -> str:
    return ensure_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def basic_utc(value: datetime) -> str:
    return format_z(value).replace("-", "").replace(":", "").replace("T", "t").replace("Z", "z")


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must carry an offset")
    if value.microsecond:
        raise ValueError("fractional seconds are not accepted")
    return value.astimezone(UTC)


def parse_rfc3339(value: str) -> datetime:
    if not RFC3339_SECONDS.fullmatch(value):
        raise ValueError("timestamp must be RFC 3339 whole seconds")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ensure_utc(parsed)


def _local_midnight(day: date, zone: ZoneInfo) -> datetime:
    wall = datetime.combine(day, time.min)
    candidates = [wall.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
    valid = [candidate for candidate in candidates if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == wall]
    by_offset = {candidate.utcoffset(): candidate for candidate in valid}
    if len(by_offset) != 1:
        raise ValueError("local midnight is not a valid unique time")
    return next(iter(by_offset.values()))


def validate_local_date(day: date, timezone: str) -> None:
    _local_midnight(day, ZoneInfo(timezone))


def build_period(kind: str, timezone: str, *, now: datetime | None = None, start: str | None = None, end: str | None = None) -> ReportPeriod:
    zone = ZoneInfo(timezone)
    if kind == "explicit":
        if start is None or end is None:
            raise ValueError("explicit period requires both boundaries")
        start_dt = datetime.fromisoformat(parse_rfc3339(start).isoformat())
        end_dt = datetime.fromisoformat(parse_rfc3339(end).isoformat())
        ensure_utc(start_dt)
        ensure_utc(end_dt)
        if end_dt <= start_dt:
            raise ValueError("period start must be before end")
        if end_dt.astimezone(UTC) - start_dt.astimezone(UTC) > timedelta(days=365):
            raise ValueError("explicit period cannot exceed 365 days")
        return ReportPeriod(kind, timezone, start_dt.astimezone(zone), end_dt.astimezone(zone))
    if kind not in {"weekly", "monthly"}:
        raise ValueError("period must be weekly, monthly, or explicit")
    current = (now or datetime.now(zone)).astimezone(zone)
    today = current.date()
    if kind == "weekly":
        this_monday = today - timedelta(days=today.weekday())
        local_start = _local_midnight(this_monday - timedelta(days=7), zone)
        local_end = _local_midnight(this_monday, zone)
    else:
        this_month = today.replace(day=1)
        previous_month_end = this_month - timedelta(days=1)
        local_start = _local_midnight(previous_month_end.replace(day=1), zone)
        local_end = _local_midnight(this_month, zone)
    return ReportPeriod(kind, timezone, local_start, local_end)


def effective_window(period: ReportPeriod, active_from: date, active_until: date | None) -> tuple[datetime, datetime] | None:
    zone = ZoneInfo(period.timezone)
    member_start = _local_midnight(active_from, zone)
    member_end = _local_midnight(active_until, zone) if active_until else datetime.max.replace(tzinfo=zone)
    start = max(period.start_local, member_start)
    end = min(period.end_local, member_end)
    return (start, end) if start < end else None
