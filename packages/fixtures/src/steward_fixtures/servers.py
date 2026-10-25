"""The fixture world as MCP tool servers, one per source (Phase 1 D5: in process).

Steward's tool door connects to each server with the SDK's in-memory client, lists its tools,
pins them by fingerprint and forwards the calls it allows. The servers return the full data of
their own side and know nothing about Steward: the door decides who may call what, holds what
needs the owner, and executes approved calls. Phase 2 adds the write tools (send mail, book and
change travel, update and delete to-do items) and the personal needs profile; all of them work
on a World, which a scenario or a test resets.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from steward_fixtures.loader import CalendarEvent, MailMessage, Side, load_raw, resolve_anchor
from steward_fixtures.world import World

# Upstream server name -> the side whose data it serves.
CALENDARS: dict[str, Side] = {"work_calendar": "work", "personal_calendar": "personal"}
MAILBOXES: dict[str, Side] = {"work_mail": "work", "personal_gmail": "personal"}
SERVER_NAMES = (*CALENDARS, *MAILBOXES, "shared_todo", "travel", "personal_profile")
MAX_DAYS = 31


def event_json(event: CalendarEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "location": event.location,
        "notes": event.notes,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "can_move": event.can_move,
    }


def message_summary(message: MailMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "received": message.received.isoformat(),
        "from": message.sender,
        "subject": message.subject,
        "labels": list(message.labels),
        "trust": message.trust,
    }


def calendar_server(name: str, side: Side, world: World) -> MCPServer:
    server: MCPServer = MCPServer(name=name, instructions=f"The {side} calendar (synthetic).")

    @server.tool(
        description=(
            "List calendar events between two dates, inclusive. Dates are YYYY-MM-DD; times "
            "are Europe/London."
        )
    )
    def list_events(start_date: str, end_date: str) -> dict[str, Any]:
        start, end = dt.date.fromisoformat(start_date), dt.date.fromisoformat(end_date)
        if end < start:
            raise ToolError("end_date is before start_date")
        if (end - start).days >= MAX_DAYS:
            raise ToolError(f"at most {MAX_DAYS} days per call")
        return {"events": [event_json(e) for e in world.events_between(side, start, end)]}

    return server


def mail_server(name: str, side: Side, world: World) -> MCPServer:
    server: MCPServer = MCPServer(name=name, instructions=f"The {side} mailbox (synthetic).")

    @server.tool(description="List the messages in the mailbox, oldest first, without bodies.")
    def list_messages() -> dict[str, Any]:
        return {"messages": [message_summary(m) for m in world.messages(side)]}

    @server.tool(description="Read one message by its id, including the body.")
    def read_message(id: str) -> dict[str, Any]:
        for message in world.messages(side):
            if message.id == id:
                return {
                    **message_summary(message),
                    "to": message.to,
                    "body": message.body,
                    "body_html": message.body_html,
                }
        raise ToolError(f"no message with id {id!r}")

    if side == "work":

        @server.tool(description="Send an email from the work mailbox.")
        def send_message(to: str, subject: str, body: str) -> dict[str, Any]:
            if "@" not in to:
                raise ToolError("to must be an email address")
            return {"sent": world.send(to, subject, body)}

    return server


def todo_server(world: World) -> MCPServer:
    server: MCPServer = MCPServer(
        name="shared_todo", instructions="The shared to-do list (synthetic)."
    )

    @server.tool(description="List the shared to-do items. Each item carries a version number.")
    def list_items() -> dict[str, Any]:
        return {"items": [world.todo_item(item_id) for item_id in world.todo]}

    @server.tool(description="Read one to-do item, with its current version.")
    def get_item(id: str) -> dict[str, Any]:
        return {"item": world.todo_item(id)}

    @server.tool(
        description=(
            "Update a to-do item. fields may set title (text) and done (true or false). "
            "expected_version is the version you read; the update fails if the item changed "
            "since then."
        )
    )
    def update_item(id: str, fields: dict[str, Any], expected_version: int) -> dict[str, Any]:
        return {"item": world.update_todo(id, fields, expected_version)}

    @server.tool(description="Delete a to-do item.")
    def delete_item(id: str) -> dict[str, Any]:
        return world.delete_todo(id)

    return server


def travel_server(world: World) -> MCPServer:
    server: MCPServer = MCPServer(
        name="travel",
        instructions="Work travel: trains and taxis (synthetic timetable, prices in GBP).",
    )

    @server.tool(
        description=(
            "Search trains on a date (YYYY-MM-DD). origin and destination are optional "
            "station names."
        )
    )
    def search_trains(
        date: str, origin: str | None = None, destination: str | None = None
    ) -> dict[str, Any]:
        day = dt.date.fromisoformat(date)
        options = world.trains(day)
        if origin:
            options = [o for o in options if origin.lower() in o["from"].lower()]
        if destination:
            options = [o for o in options if destination.lower() in o["to"].lower()]
        taxi = world.taxi()
        return {
            "trains": options,
            "taxi": {
                "to": taxi["to"],
                "minutes": taxi["minutes"],
                "price_gbp": taxi["price_gbp"],
                "pickup_before_departure_minutes": taxi["pickup_before_departure_minutes"],
                "from": "the traveller's home address",
            },
            "booking_limit_gbp": load_raw("travel_options")["booking_limit_gbp"],
        }

    @server.tool(description="List the bookings made so far.")
    def list_bookings() -> dict[str, Any]:
        return {"bookings": list(world.bookings.values())}

    @server.tool(
        description=(
            "Price a booking call before making it: tool is book_train, book_taxi or "
            "change_booking, arguments are that call's arguments."
        )
    )
    def quote(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return world.quote(tool, arguments)

    @server.tool(description="Book a train by its option id (from search_trains).")
    def book_train(option_id: str) -> dict[str, Any]:
        return {"booking": world.book_train(option_id)}

    @server.tool(
        description=(
            "Book a taxi from the traveller's home to the station on a date (YYYY-MM-DD) at a "
            "pickup time (HH:MM). address is the pickup address."
        )
    )
    def book_taxi(date: str, pickup_time: str, address: str) -> dict[str, Any]:
        return {"booking": world.book_taxi(date, pickup_time, address)}

    @server.tool(
        description=(
            "Change a booking: a train by option_id (same day), a taxi by pickup_time (HH:MM). "
            "address is optional for a taxi."
        )
    )
    def change_booking(
        booking_id: str,
        option_id: str | None = None,
        pickup_time: str | None = None,
        address: str | None = None,
    ) -> dict[str, Any]:
        return {"booking": world.change_booking(booking_id, option_id, pickup_time, address)}

    return server


def profile_server(world: World) -> MCPServer:
    server: MCPServer = MCPServer(
        name="personal_profile", instructions="The owner's private needs profile (synthetic)."
    )

    @server.tool(
        description=(
            "Read the owner's needs profile: travel limits, dietary and access needs (with "
            "reasons) and the home address."
        )
    )
    def read_needs() -> dict[str, Any]:
        raw = load_raw("needs_profile")
        return {
            "travel_limits": raw["travel_limits"],
            "dietary": raw["dietary"],
            "access": raw["access"],
            "home_address": raw["home_address"],
        }

    return server


def build_servers(world: World | dt.date | None = None) -> dict[str, MCPServer]:
    """Every fixture server by upstream name, sharing one World (a fresh one from an anchor)."""
    if not isinstance(world, World):
        world = World(world or resolve_anchor())
    servers: dict[str, MCPServer] = {}
    for name, side in CALENDARS.items():
        servers[name] = calendar_server(name, side, world)
    for name, side in MAILBOXES.items():
        servers[name] = mail_server(name, side, world)
    servers["shared_todo"] = todo_server(world)
    servers["travel"] = travel_server(world)
    servers["personal_profile"] = profile_server(world)
    return servers


def demo_window(anchor: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The dates the fixtures cover: day 0 to day 27 (four weeks)."""
    day0 = anchor or resolve_anchor()
    return day0, day0 + dt.timedelta(days=27)
