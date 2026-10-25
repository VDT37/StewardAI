"""Timestamps, the Europe/London day that limits and spend are counted in, and Steward's clock.

Every rule reads time from one injectable clock (Phase 2). Normally it is the system clock. A
scenario sets story time (Sun 11 Oct 19:00 and onwards) and replay sets the recorded time of each
action, so "today", the daily counters and the 04:00 flag reset come out the same both times.
Times are cut to whole milliseconds, the precision the flight log stores, so a replay that sets
the clock from a logged timestamp sees exactly the instant the live run saw.
"""

from __future__ import annotations

import time as _time
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
# Trifecta flags clear at this London time each day (P2-D2).
FLAG_RESET = time(4, 0)


def truncate_ms(moment: datetime) -> datetime:
    return moment.replace(microsecond=moment.microsecond - moment.microsecond % 1000)


def utc_now() -> datetime:
    return truncate_ms(datetime.now(UTC))


def iso_ms(moment: datetime) -> str:
    """UTC ISO 8601 with milliseconds and a Z, so stored timestamps sort as text."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def london_day(moment: datetime) -> date:
    return moment.astimezone(LONDON).date()


def london_day_start(day: date) -> str:
    """The UTC timestamp (iso_ms format) at which a London day starts."""
    return iso_ms(datetime.combine(day, time.min, tzinfo=LONDON))


def london_day_end(day: date) -> str:
    return london_day_start(day + timedelta(days=1))


def last_flag_reset(moment: datetime) -> datetime:
    """The most recent 04:00 Europe/London at or before moment."""
    local = moment.astimezone(LONDON)
    reset = datetime.combine(local.date(), FLAG_RESET, tzinfo=LONDON)
    if reset > local:
        reset = datetime.combine(local.date() - timedelta(days=1), FLAG_RESET, tzinfo=LONDON)
    return reset


class Clock(Protocol):
    def now(self) -> datetime: ...


class StewardClock:
    """System time by default. set() switches to story time: now = at + elapsed * rate.

    rate 1 runs story time at real speed (agents at work), a larger rate fast-forwards the quiet
    gaps between scripted beats, and rate 0 freezes it (replay sets each instant itself).
    """

    def __init__(self) -> None:
        self._base: datetime | None = None
        self._mono = 0.0
        self.rate = 1.0

    @property
    def mode(self) -> str:
        return "system" if self._base is None else "story"

    def now(self) -> datetime:
        if self._base is None:
            return utc_now()
        elapsed = (_time.monotonic() - self._mono) * self.rate
        return truncate_ms(self._base + timedelta(seconds=elapsed))

    def set(self, at: datetime, rate: float = 1.0) -> None:
        if at.tzinfo is None:
            raise ValueError("clock times must be timezone-aware")
        self._base = truncate_ms(at.astimezone(UTC))
        self._mono = _time.monotonic()
        self.rate = rate

    def reset(self) -> None:
        self._base = None
        self.rate = 1.0

    def describe(self) -> dict[str, Any]:
        now = self.now()
        return {
            "mode": self.mode,
            "rate": self.rate,
            "now": iso_ms(now),
            "london": now.astimezone(LONDON).isoformat(timespec="seconds"),
        }
