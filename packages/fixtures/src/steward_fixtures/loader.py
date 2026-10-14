"""Load the fixture JSON and resolve its anchor-relative dates.

Fixture files never store calendar dates. Each event, message or scripted change stores a day
offset from an anchor Monday (day 0) and a local time in Europe/London. The anchor is chosen
when the data is loaded (ADR 0012):

- "replay" pins day 0 to Monday 12 October 2026, so replays, the trap suite and the video are
  repeatable.
- "next-monday" (the default) puts day 0 on the first Monday after today, so the playground and
  live mode always show a week in the future.
- An ISO date such as "2026-11-02" pins day 0 to that Monday.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cache
from importlib import resources
from typing import Any, Literal
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/London")
REPLAY_ANCHOR = date(2026, 10, 12)
ANCHOR_ENV = "STEWARD_DEMO_ANCHOR"

FIXTURE_FILES = (
    "work_calendar",
    "personal_calendar",
    "work_mail",
    "personal_gmail",
    "shared_todo",
    "needs_profile",
    "overnight_script",
    "web_pages",
    "travel_options",
    "planted",
)

Side = Literal["work", "personal"]
_CALENDAR_FILES: dict[str, str] = {"work": "work_calendar", "personal": "personal_calendar"}
_MAIL_FILES: dict[str, str] = {"work": "work_mail", "personal": "personal_gmail"}


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    side: str
    title: str
    location: str
    notes: str
    start: datetime
    end: datetime
    can_move: bool


@dataclass(frozen=True)
class MailMessage:
    id: str
    side: str
    received: datetime
    sender: str
    to: str
    subject: str
    body: str
    body_html: str | None
    labels: tuple[str, ...]
    trust: str


@dataclass(frozen=True)
class ScriptedChange:
    id: str
    at: datetime
    kind: str
    side: str
    data: dict[str, Any]


def resolve_anchor(value: str | None = None, today: date | None = None) -> date:
    """Return the date of fixture day 0, which is always a Monday.

    value: "replay", "next-monday" or an ISO date. None reads STEWARD_DEMO_ANCHOR and falls back
    to "next-monday". today: only used by "next-monday"; defaults to today in Europe/London.
    """
    raw = value if value is not None else os.environ.get(ANCHOR_ENV, "next-monday")
    if raw == "replay":
        return REPLAY_ANCHOR
    if raw == "next-monday":
        current = today or datetime.now(TZ).date()
        return current + timedelta(days=(7 - current.weekday()) % 7 or 7)
    anchor = date.fromisoformat(raw)
    if anchor.weekday() != 0:
        raise ValueError(f"{ANCHOR_ENV} must be a Monday, got {anchor.isoformat()}")
    return anchor


@cache
def load_raw(name: str) -> dict[str, Any]:
    """Return one fixture file (name without .json) as parsed JSON. Cached: treat as read-only."""
    if name not in FIXTURE_FILES:
        raise KeyError(f"unknown fixture file: {name}")
    text = resources.files("steward_fixtures").joinpath("data", f"{name}.json").read_text("utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def at(anchor: date, day: int, hhmm: str) -> datetime:
    """The timezone-aware local datetime for a day offset and an HH:MM time."""
    return datetime.combine(anchor + timedelta(days=day), time.fromisoformat(hhmm), tzinfo=TZ)


def calendar_events(side: Side, anchor: date | None = None) -> list[CalendarEvent]:
    """Every event on one side's calendar, recurring events expanded, sorted by start.

    Events added later by the overnight script are not included; see overnight_changes().
    """
    day0 = anchor or resolve_anchor()
    events: list[CalendarEvent] = []
    for item in load_raw(_CALENDAR_FILES[side])["events"]:
        events.extend(_expand(item, side, day0))
    return sorted(events, key=lambda e: (e.start, e.id))


def events_between(
    side: Side, start: date, end: date, anchor: date | None = None
) -> list[CalendarEvent]:
    """Events that overlap the inclusive date range [start, end]."""
    lo = datetime.combine(start, time.min, tzinfo=TZ)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=TZ)
    return [e for e in calendar_events(side, anchor) if e.start < hi and e.end > lo]


def mail_messages(side: Side, anchor: date | None = None) -> list[MailMessage]:
    """Every message in one side's mailbox, oldest first."""
    day0 = anchor or resolve_anchor()
    messages = [
        MailMessage(
            id=m["id"],
            side=side,
            received=at(day0, m["day"], m["time"]),
            sender=m["from"],
            to=m["to"],
            subject=m["subject"],
            body=m["body"],
            body_html=m["body_html"],
            labels=tuple(m["labels"]),
            trust=m["trust"],
        )
        for m in load_raw(_MAIL_FILES[side])["messages"]
    ]
    return sorted(messages, key=lambda m: (m.received, m.id))


def overnight_changes(anchor: date | None = None) -> list[ScriptedChange]:
    """The scripted changes of the Manchester evening, in time order."""
    day0 = anchor or resolve_anchor()
    changes = [
        ScriptedChange(
            id=c["id"],
            at=at(day0, c["day"], c["time"]),
            kind=c["kind"],
            side=c["side"],
            data={k: v for k, v in c.items() if k not in {"id", "day", "time", "kind", "side"}},
        )
        for c in load_raw("overnight_script")["changes"]
    ]
    return sorted(changes, key=lambda c: (c.at, c.id))


def expand_event(item: dict[str, Any], side: str, anchor: date) -> list[CalendarEvent]:
    """Expand one raw event (for example from a scripted change) into concrete occurrences."""
    return _expand(item, side, anchor)


def planted_details(side: str | None = None) -> list[dict[str, str]]:
    """The planted-detail registry, optionally filtered to the side that owns the detail."""
    details: list[dict[str, str]] = load_raw("planted")["details"]
    return [d for d in details if side is None or d["side"] == side]


def find_planted(text: str, side: str | None = None) -> list[str]:
    """IDs of planted details found in text (case-insensitive substring match)."""
    lowered = text.lower()
    return [d["id"] for d in planted_details(side) if d["text"].lower() in lowered]


def _expand(item: dict[str, Any], side: str, anchor: date) -> list[CalendarEvent]:
    days: list[int] = item["days"]
    return [
        CalendarEvent(
            id=item["id"] if len(days) == 1 else f"{item['id']}-d{day}",
            side=side,
            title=item["title"],
            location=item["location"],
            notes=item["notes"],
            start=at(anchor, day, item["start"]),
            end=at(anchor, day, item["end"]),
            can_move=item["can_move"],
        )
        for day in days
    ]
