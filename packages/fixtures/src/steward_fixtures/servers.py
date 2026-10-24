"""The fixture data as MCP tool servers, one per source (Phase 1 D5: in process).

Steward's tool door connects to each server with the SDK's in-memory client, lists its tools,
pins them by fingerprint and forwards the calls it allows. The servers are read-only in
Phase 1. They return the full data of their own side; the door decides who may call them.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from mcp.server import MCPServer

from steward_fixtures.loader import (
    CalendarEvent,
    MailMessage,
    Side,
    at,
    events_between,
    load_raw,
    mail_messages,
    resolve_anchor,
)

# Upstream server name -> the side whose data it serves.
CALENDARS: dict[str, Side] = {"work_calendar": "work", "personal_calendar": "personal"}
MAILBOXES: dict[str, Side] = {"work_mail": "work", "personal_gmail": "personal"}
SERVER_NAMES = (*CALENDARS, *MAILBOXES, "shared_todo")
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


def calendar_server(name: str, side: Side, anchor: date) -> MCPServer:
    server: MCPServer = MCPServer(name=name, instructions=f"The {side} calendar (synthetic).")

    @server.tool(
        description=(
            "List calendar events between two dates, inclusive. Dates are YYYY-MM-DD; times "
            "are Europe/London."
        )
    )
    def list_events(start_date: str, end_date: str) -> dict[str, Any]:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if end < start:
            raise ValueError("end_date is before start_date")
        if (end - start).days >= MAX_DAYS:
            raise ValueError(f"at most {MAX_DAYS} days per call")
        return {"events": [event_json(e) for e in events_between(side, start, end, anchor)]}

    return server


def mail_server(name: str, side: Side, anchor: date) -> MCPServer:
    server: MCPServer = MCPServer(name=name, instructions=f"The {side} mailbox (synthetic).")

    @server.tool(description="List the messages in the mailbox, oldest first, without bodies.")
    def list_messages() -> dict[str, Any]:
        return {"messages": [message_summary(m) for m in mail_messages(side, anchor)]}

    @server.tool(description="Read one message by its id, including the body.")
    def read_message(id: str) -> dict[str, Any]:
        for message in mail_messages(side, anchor):
            if message.id == id:
                return {
                    **message_summary(message),
                    "to": message.to,
                    "body": message.body,
                    "body_html": message.body_html,
                }
        raise ValueError(f"no message with id {id!r}")

    return server


def todo_server(anchor: date) -> MCPServer:
    server: MCPServer = MCPServer(
        name="shared_todo", instructions="The shared to-do list (synthetic)."
    )

    @server.tool(description="List the shared to-do items. Each item carries a version number.")
    def list_items() -> dict[str, Any]:
        items = []
        for item in load_raw("shared_todo")["items"]:
            modified = at(anchor, item["last_modified_day"], item["last_modified_time"])
            items.append(
                {
                    "id": item["id"],
                    "title": item["title"],
                    "done": item["done"],
                    "version": item["version"],
                    "last_modified_by": item["last_modified_by"],
                    "last_modified": modified.isoformat(),
                }
            )
        return {"items": items}

    return server


def build_servers(anchor: date | None = None) -> dict[str, MCPServer]:
    """Every fixture server by upstream name, with dates resolved from the anchor."""
    day0 = anchor or resolve_anchor()
    servers: dict[str, MCPServer] = {}
    for name, side in CALENDARS.items():
        servers[name] = calendar_server(name, side, day0)
    for name, side in MAILBOXES.items():
        servers[name] = mail_server(name, side, day0)
    servers["shared_todo"] = todo_server(day0)
    return servers


def demo_window(anchor: date | None = None) -> tuple[date, date]:
    """The dates the fixtures cover: day 0 to day 27 (four weeks)."""
    day0 = anchor or resolve_anchor()
    return day0, day0 + timedelta(days=27)
