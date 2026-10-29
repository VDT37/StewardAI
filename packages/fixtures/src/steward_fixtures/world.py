"""The mutable fixture world (Phase 2): the state the write tools change and the story script
moves forward.

The JSON fixtures stay frozen; a World is a working copy. reset() puts it back to the fixtures,
so every scenario run, replay and trap starts from the same state. When follow_clock is on (a
scenario or its replay), mail is visible only once its received time has passed on the world's
clock, so the poisoned agenda appears at 22:40 story time. The scripted changes in
overnight_script.json are applied with apply_change().

The home address lives here because the taxi company needs it. Steward keeps it from the work
agent: it swaps a stand-in for the real address only when it executes a booking, and scrubs
the address out of every result the work agent gets.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from steward_fixtures.loader import (
    TZ,
    CalendarEvent,
    MailMessage,
    Side,
    at,
    calendar_events,
    expand_event,
    load_raw,
    mail_messages,
    overnight_changes,
)

Listener = Callable[[str, dict[str, Any]], None]
TODO_FIELDS = ("title", "done")


class WorldError(ToolError):
    """A call the fixture service itself rejects (unknown id, version conflict, bad input)."""


def home_address() -> str:
    """The home address from the needs profile, as one line."""
    raw = load_raw("needs_profile")["home_address"]
    return f"{raw['line1']}, {raw['city']}, {raw['postcode']}"


def home_address_parts() -> list[str]:
    """The strings that identify the home address, longest first, for scrubbing results."""
    raw = load_raw("needs_profile")["home_address"]
    parts = [home_address(), raw["line1"], raw["postcode"]]
    return sorted(set(parts), key=len, reverse=True)


class World:
    def __init__(
        self,
        anchor: date,
        clock: Callable[[], datetime] | None = None,
        *,
        follow_clock: bool = False,
    ) -> None:
        self.anchor = anchor
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(TZ))
        self._listeners: list[Listener] = []
        self.follow_clock = follow_clock
        self.added: dict[str, list[dict[str, Any]]] = {}
        self.todo: dict[str, dict[str, Any]] = {}
        self.bookings: dict[str, dict[str, Any]] = {}
        self.outbox: list[dict[str, Any]] = []
        self.applied: list[str] = []
        # Messages an agent reported as suspicious (Phase 3), by message id.
        self.reported: dict[str, dict[str, Any]] = {}
        # Steward pins the world's time to the moment it decided a call while that call runs,
        # so a replay (which decides at the recorded moment) sees exactly the same time.
        self.pinned: datetime | None = None
        self.reset(follow_clock=follow_clock)

    def reset(self, *, follow_clock: bool | None = None) -> None:
        if follow_clock is not None:
            self.follow_clock = follow_clock
        self.added = {"work": [], "personal": []}
        self.todo = {}
        for item in load_raw("shared_todo")["items"]:
            entry = copy.deepcopy(item)
            entry["last_modified"] = at(
                self.anchor, item["last_modified_day"], item["last_modified_time"]
            ).isoformat()
            self.todo[item["id"]] = entry
        self.bookings = {}
        self.outbox = []
        self.applied = []
        self.reported = {}

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _changed(self, kind: str, data: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            listener(kind, data)

    def now(self) -> datetime:
        return (self.pinned or self.clock()).astimezone(TZ)

    # Calendars and mail -----------------------------------------------------------------------

    def events_between(self, side: Side, start: date, end: date) -> list[CalendarEvent]:
        events = list(calendar_events(side, self.anchor))
        for raw in self.added[side]:
            events.extend(expand_event(raw, side, self.anchor))
        lo = datetime.combine(start, datetime.min.time(), tzinfo=TZ)
        hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=TZ)
        found = [e for e in events if e.start < hi and e.end > lo]
        return sorted(found, key=lambda e: (e.start, e.id))

    def messages(self, side: Side) -> list[MailMessage]:
        messages = mail_messages(side, self.anchor)
        if self.follow_clock:
            now = self.now()
            messages = [m for m in messages if m.received <= now]
        return messages

    def report(self, side: Side, message_id: str, reason: str) -> dict[str, Any]:
        """Mark a message as reported (phishing or spam), like a mail client's Report button.
        Reporting twice keeps the first report."""
        if not any(m.id == message_id for m in self.messages(side)):
            raise WorldError(f"no message with id {message_id!r}")
        if message_id not in self.reported:
            self.reported[message_id] = {
                "id": message_id,
                "side": side,
                "reason": reason[:300],
                "at": self.now().isoformat(),
            }
            self._changed("mail_reported", {"side": side, "id": message_id})
        return dict(self.reported[message_id])

    def send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        message = {
            "id": f"sent-{len(self.outbox) + 1}",
            "to": to,
            "subject": subject,
            "body": body,
            "sent": self.now().isoformat(),
        }
        self.outbox.append(message)
        self._changed("mail_sent", {"id": message["id"]})
        return message

    # Shared to-do list ------------------------------------------------------------------------

    def todo_item(self, item_id: str) -> dict[str, Any]:
        item = self.todo.get(item_id)
        if item is None:
            raise WorldError(f"no to-do item with id {item_id!r}")
        return _todo_public(item)

    def update_todo(
        self, item_id: str, fields: dict[str, Any], expected_version: int
    ) -> dict[str, Any]:
        item = self.todo.get(item_id)
        if item is None:
            raise WorldError(f"no to-do item with id {item_id!r}")
        unknown = sorted(set(fields) - set(TODO_FIELDS))
        if unknown or not fields:
            raise WorldError(f"fields may only set {', '.join(TODO_FIELDS)}")
        if expected_version != item["version"]:
            raise WorldError(
                f"version conflict: you read version {expected_version}, current is "
                f"{item['version']}"
            )
        for key, value in fields.items():
            if key == "title" and not (isinstance(value, str) and value.strip()):
                raise WorldError("title must be a non-empty string")
            if key == "done" and not isinstance(value, bool):
                raise WorldError("done must be true or false")
            item[key] = value
        item["version"] += 1
        item["last_modified_by"] = "agent"
        item["last_modified"] = self.now().isoformat()
        self._changed("todo_updated", {"id": item_id, "version": item["version"]})
        return _todo_public(item)

    def delete_todo(self, item_id: str) -> dict[str, Any]:
        item = self.todo.pop(item_id, None)
        if item is None:
            raise WorldError(f"no to-do item with id {item_id!r}")
        self._changed("todo_deleted", {"id": item_id})
        return {"deleted": item_id}

    # Travel -----------------------------------------------------------------------------------

    def trains(self, day: date | None = None) -> list[dict[str, Any]]:
        options = []
        for train in load_raw("travel_options")["trains"]:
            when = self.anchor + timedelta(days=train["day"])
            if day is not None and when != day:
                continue
            options.append(
                {
                    "id": train["id"],
                    "date": when.isoformat(),
                    "from": train["from"],
                    "to": train["to"],
                    "depart": train["depart"],
                    "arrive": train["arrive"],
                    "price_gbp": train["price_gbp"],
                }
            )
        return options

    def train(self, option_id: str) -> dict[str, Any]:
        for option in self.trains():
            if option["id"] == option_id:
                return option
        raise WorldError(f"no train with id {option_id!r}")

    def taxi(self) -> dict[str, Any]:
        taxi: dict[str, Any] = load_raw("travel_options")["taxis"][0]
        return taxi

    def quote(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Price a prospective booking call and name its trip date, without booking."""
        if tool in ("book_train", "travel_book_train"):
            option = self.train(str(arguments.get("option_id")))
            return {"price_gbp": option["price_gbp"], "trip_date": option["date"], "item": option}
        if tool in ("book_taxi", "travel_book_taxi"):
            trip = _date_arg(arguments.get("date"))
            return {
                "price_gbp": self.taxi()["price_gbp"],
                "trip_date": trip.isoformat(),
                "item": {"to": self.taxi()["to"], "pickup_time": arguments.get("pickup_time")},
            }
        if tool in ("change_booking", "travel_change_booking"):
            booking = self._booking(str(arguments.get("booking_id")))
            price = booking["price_gbp"]
            if booking["kind"] == "train" and arguments.get("option_id"):
                price = self.train(str(arguments["option_id"]))["price_gbp"]
            return {"price_gbp": price, "trip_date": booking["date"], "item": booking}
        raise WorldError(f"cannot quote {tool!r}")

    def _booking(self, booking_id: str) -> dict[str, Any]:
        booking = self.bookings.get(booking_id)
        if booking is None:
            raise WorldError(f"no booking with id {booking_id!r}")
        return booking

    def _new_booking(self, data: dict[str, Any]) -> dict[str, Any]:
        booking_id = f"bk-{len(self.bookings) + 1}"
        booking = {"booking_id": booking_id, "status": "confirmed", "changes": 0, **data}
        self.bookings[booking_id] = booking
        self._changed("booking_made", {"booking_id": booking_id})
        return copy.deepcopy(booking)

    def book_train(self, option_id: str) -> dict[str, Any]:
        option = self.train(option_id)
        return self._new_booking(
            {
                "kind": "train",
                "option_id": option_id,
                "date": option["date"],
                "from": option["from"],
                "to": option["to"],
                "depart": option["depart"],
                "arrive": option["arrive"],
                "price_gbp": option["price_gbp"],
            }
        )

    def book_taxi(self, trip_date: str, pickup_time: str, address: str) -> dict[str, Any]:
        day = _date_arg(trip_date)
        _time_arg(pickup_time)
        if address.strip().lower() != home_address().lower():
            raise WorldError("the taxi only serves the home address on file")
        return self._new_booking(
            {
                "kind": "taxi",
                "date": day.isoformat(),
                "pickup_time": pickup_time,
                "from": address,
                "to": self.taxi()["to"],
                "price_gbp": self.taxi()["price_gbp"],
            }
        )

    def change_booking(
        self,
        booking_id: str,
        option_id: str | None = None,
        pickup_time: str | None = None,
        address: str | None = None,
    ) -> dict[str, Any]:
        booking = self._booking(booking_id)
        if booking["kind"] == "train":
            if not option_id or pickup_time or address:
                raise WorldError("a train booking changes by option_id only")
            option = self.train(option_id)
            if option["date"] != booking["date"]:
                raise WorldError("the new train must be on the same day")
            booking.update(
                {
                    "option_id": option_id,
                    "depart": option["depart"],
                    "arrive": option["arrive"],
                    "price_gbp": option["price_gbp"],
                }
            )
        else:
            if option_id or not pickup_time:
                raise WorldError("a taxi booking changes by pickup_time")
            _time_arg(pickup_time)
            if address is not None and address.strip().lower() != home_address().lower():
                raise WorldError("the taxi only serves the home address on file")
            booking["pickup_time"] = pickup_time
        booking["changes"] += 1
        self._changed("booking_changed", {"booking_id": booking_id})
        return copy.deepcopy(booking)

    # Scripted changes -------------------------------------------------------------------------

    def apply_change(self, change_id: str) -> dict[str, Any]:
        """Apply one change from overnight_script.json to the world. Changes the agents make
        themselves (the to-do writes) are played through the doors instead, not here."""
        change = next((c for c in overnight_changes(self.anchor) if c.id == change_id), None)
        if change is None:
            raise WorldError(f"no scripted change {change_id!r}")
        self.applied.append(change_id)
        if change.kind == "calendar_event_added":
            event = dict(change.data["event"])
            self.added[change.side].append(event)
            self._changed("calendar_changed", {"side": change.side, "event_id": event["id"]})
        elif change.kind == "mail_received":
            self._changed("mail_received", {"side": change.side, "ref": change.data.get("ref")})
        return {
            "id": change.id,
            "kind": change.kind,
            "side": change.side,
            "at": change.at.isoformat(),
        }


def _todo_public(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "title": item["title"],
        "done": item["done"],
        "version": item["version"],
        "last_modified_by": item["last_modified_by"],
        "last_modified": item["last_modified"],
    }


def _date_arg(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise WorldError("date must be YYYY-MM-DD") from exc


def _time_arg(value: Any) -> str:
    text = str(value)
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError as exc:
        raise WorldError("times must be HH:MM") from exc
    return text
