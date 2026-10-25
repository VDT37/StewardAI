import asyncio
from datetime import datetime
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult

from steward_fixtures import TZ, World, WorldError, home_address, resolve_anchor
from steward_fixtures.servers import SERVER_NAMES, build_servers, demo_window

ANCHOR = resolve_anchor("replay")


def call(server_name: str, tool: str, args: dict[str, Any], world: World | None = None) -> Any:
    async def run() -> CallToolResult:
        async with Client(build_servers(world or World(ANCHOR))[server_name]) as client:
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
    assert names["work_mail"] == ["list_messages", "read_message", "send_message"]
    assert names["personal_gmail"] == ["list_messages", "read_message"]
    assert names["shared_todo"] == ["delete_item", "get_item", "list_items", "update_item"]
    assert names["travel"] == [
        "book_taxi",
        "book_train",
        "change_booking",
        "list_bookings",
        "quote",
        "search_trains",
    ]
    assert names["personal_profile"] == ["read_needs"]


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


def test_todo_update_checks_the_version() -> None:
    world = World(ANCHOR)
    done = call(
        "shared_todo",
        "update_item",
        {"id": "todo-expenses", "fields": {"done": True}, "expected_version": 1},
        world,
    )
    assert done.structured_content["item"]["version"] == 2
    stale = call(
        "shared_todo",
        "update_item",
        {"id": "todo-expenses", "fields": {"title": "x"}, "expected_version": 1},
        world,
    )
    assert stale.is_error and "version conflict" in stale.content[0].text
    with pytest.raises(WorldError):
        world.update_todo("todo-expenses", {"colour": "red"}, 2)
    world.reset()
    assert world.todo_item("todo-expenses")["version"] == 1


def test_mail_follows_the_clock_in_a_scenario() -> None:
    sunday_evening = datetime(2026, 10, 11, 19, 0, tzinfo=TZ)
    world = World(ANCHOR, clock=lambda: sunday_evening, follow_clock=True)
    ids = [m.id for m in world.messages("work")]
    assert "w-msg-visit-request" in ids and "w-msg-poisoned-agenda" not in ids
    world.clock = lambda: datetime(2026, 10, 11, 22, 41, tzinfo=TZ)
    assert "w-msg-poisoned-agenda" in [m.id for m in world.messages("work")]


def test_travel_books_quotes_and_changes() -> None:
    world = World(ANCHOR)
    trains = world.trains(ANCHOR.replace(day=15))
    assert [t["id"] for t in trains][:2] == ["train-0752", "train-0930"]
    assert world.quote("book_train", {"option_id": "train-0930"})["price_gbp"] == 89
    train = world.book_train("train-0930")
    taxi = world.book_taxi("2026-10-15", "09:00", home_address())
    assert train["booking_id"] == "bk-1" and taxi["price_gbp"] == 14
    with pytest.raises(WorldError):
        world.book_taxi("2026-10-15", "09:00", "1 Other Street")
    moved = world.change_booking("bk-1", option_id="train-1130")
    assert moved["depart"] == "11:30" and moved["changes"] == 1
    later = world.change_booking("bk-2", pickup_time="11:00")
    assert later["pickup_time"] == "11:00"
    assert world.quote("change_booking", {"booking_id": "bk-1"})["trip_date"] == "2026-10-15"


def test_blood_test_change_adds_an_event_and_notifies() -> None:
    world = World(ANCHOR)
    seen: list[str] = []
    world.subscribe(lambda kind, data: seen.append(kind))
    before = world.events_between("personal", ANCHOR.replace(day=15), ANCHOR.replace(day=15))
    world.apply_change("chg-blood-test-added")
    after = world.events_between("personal", ANCHOR.replace(day=15), ANCHOR.replace(day=15))
    assert len(after) == len(before) + 1 and seen == ["calendar_changed"]


def test_profile_holds_the_needs_and_the_address() -> None:
    profile = call("personal_profile", "read_needs", {}).structured_content
    assert profile["travel_limits"][0]["rule"] == "no_departures_before"
    assert profile["home_address"]["postcode"] == "EX2 9ZZ"
