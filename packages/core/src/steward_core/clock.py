"""Timestamps and the Europe/London day that daily limits and spend are counted in."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_ms(moment: datetime) -> str:
    """UTC ISO 8601 with milliseconds and a Z, so stored timestamps sort as text."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def london_day(moment: datetime) -> date:
    return moment.astimezone(LONDON).date()


def london_day_start(day: date) -> str:
    """The UTC timestamp (iso_ms format) at which a London day starts."""
    return iso_ms(datetime.combine(day, time.min, tzinfo=LONDON))


def london_day_end(day: date) -> str:
    return london_day_start(day + timedelta(days=1))
