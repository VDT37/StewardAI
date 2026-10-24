import asyncio
from typing import Any

from mcp import Client
from mcp.types import CallToolResult

from steward_fixtures import resolve_anchor
from steward_fixtures.servers import SERVER_NAMES, build_servers, demo_window

ANCHOR = resolve_anchor("replay")


def call(server_name: str, tool: str, args: dict[str, Any]) -> CallToolResult:
    async def run() -> CallToolResult:
        async with Client(build_servers(ANCHOR)[server_name]) as client:
            result = await client.call_tool(tool, args)
        assert isinstance(result, CallToolResult)
        return result

    return asyncio.run(run())


def test_every_server_lists_its_tools() -> None:
    async def run() -> dict[str, list[str]]:
        names = {}
        for name, server in build_servers(ANCHOR).items():
            async with Client(server) as client:
                names[name] = sorted(t.name for t in (await client.list_tools()).tools)
        return names

    names = asyncio.run(run())
    assert set(names) == set(SERVER_NAMES)
    assert names["personal_calendar"] == ["list_events"]
    assert names["work_mail"] == ["list_messages", "read_message"]
    assert names["shared_todo"] == ["list_items"]


def test_calendar_returns_only_its_side() -> None:
    args = {"start_date": "2026-10-14", "end_date": "2026-10-14"}
    personal = call("personal_calendar", "list_events", args).structured_content
    work = call("work_calendar", "list_events", args).structured_content
    assert any("Dr Shah" in e["title"] for e in personal["events"])
    assert not any("Dr Shah" in e["title"] for e in work["events"])
    bad = call("personal_calendar", "list_events", {"start_date": "x", "end_date": "y"})
    assert bad.is_error


def test_mail_lists_without_bodies_and_reads_one() -> None:
    listed = call("personal_gmail", "list_messages", {}).structured_content["messages"]
    assert listed and "body" not in listed[0]
    message = call("personal_gmail", "read_message", {"id": "p-msg-hospital"}).structured_content
    assert "cardiology" in message["body"].lower()
    assert call("personal_gmail", "read_message", {"id": "nope"}).is_error


def test_todo_items_carry_versions_and_window_is_four_weeks() -> None:
    items = call("shared_todo", "list_items", {}).structured_content["items"]
    assert all(isinstance(i["version"], int) for i in items)
    lo, hi = demo_window(ANCHOR)
    assert lo.isoformat() == "2026-10-12" and (hi - lo).days == 27
