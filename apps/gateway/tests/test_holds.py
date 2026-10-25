"""Holds, approvals, execute-once, the address stand-in, stale writes, budgets and notices."""

import asyncio
import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from gateway_helpers import (
    OWNER,
    WORK,
    bearer,
    call,
    chatty_personal,
    fast_policy,
    http_app,
    running,
    story_clock,
)

from steward_core.policy import Policy
from steward_fixtures import find_planted
from steward_gateway.admin import AdminContext
from steward_gateway.holds import HoldError

PAGE = "https://manchester-station-guide.example/piccadilly-step-free"
TRIP = {"start_date": "2026-10-12", "end_date": "2026-10-15"}


async def taint(ctx: AdminContext) -> None:
    """Private data, then the poisoned page: the work agent now has both trifecta flags."""
    await call(ctx, "work", "calendar_list_events", TRIP)
    await call(ctx, "work", "web_extract", {"url": PAGE})


def test_trifecta_hold_then_one_approval_runs_the_booking_once(tmp_path: Path) -> None:
    async def scenario() -> tuple[Any, ...]:
        async with running(tmp_path, clock=story_clock()) as (_, ctx, _):
            await taint(ctx)
            held = await call(ctx, "work", "travel_book_train", {"option_id": "train-0930"})
            data = held.structured_content
            hold = ctx.holds.get(data["hold_id"])
            assert hold is not None
            pending = await call(ctx, "work", "hold_status", {"hold_id": hold.hold_id})
            decided = await ctx.decide(hold.hold_id, "approve", hold.fingerprint, "test")
            again = None
            try:
                await ctx.decide(hold.hold_id, "approve", hold.fingerprint, "test")
            except HoldError as exc:
                again = exc.status
            done = await call(ctx, "work", "hold_status", {"hold_id": hold.hold_id})
            state, _ = await ctx.tool_door.execute_hold(decided, "approve")
            return data, pending, decided, again, done, state, ctx

    data, pending, decided, again, done, state, ctx = asyncio.run(scenario())
    assert data["status"] == "held" and data["rule"] == "trifecta"
    assert pending.structured_content["status"] == "pending"
    assert decided.state == "done" and again == 409
    assert done.structured_content["status"] == "done"
    assert done.structured_content["result"]["structured"]["booking"]["option_id"] == "train-0930"
    # A second execution of the same approval finds its intent and does nothing.
    assert state == "done"
    assert [e.kind for e in ctx.store.events(kind="intent.duplicate")] == ["intent.duplicate"]
    assert len(ctx.world.bookings) == 1
    (flags_event,) = [
        e
        for e in ctx.store.events(kind="tool.result")
        if "untrusted_seen" in e.payload["flags_set"]
    ]
    assert flags_event.payload["result"]["structuredContent"]["untrusted"] is True


def test_a_changed_fingerprint_voids_the_approval(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, str, AdminContext]:
        async with running(tmp_path, clock=story_clock()) as (_, ctx, _):
            held = await call(
                ctx, "work", "mail_send_message", {"to": "a@b.example", "subject": "s", "body": "b"}
            )
            hold_id = held.structured_content["hold_id"]
            status = 0
            try:
                await ctx.decide(hold_id, "approve", "0" * 64, "test")
            except HoldError as exc:
                status = exc.status
            hold = ctx.holds.get(hold_id)
            assert hold is not None
            return status, hold.state, ctx

    status, state, ctx = asyncio.run(scenario())
    assert status == 409 and state == "pending"
    assert ctx.world.outbox == []
    assert ctx.store.events(kind="hold.rejected")[0].payload["reason"] == "fingerprint mismatch"


def test_address_card_gives_a_stand_in_for_one_taxi_booking(tmp_path: Path) -> None:
    ask = {
        "contact": "personal",
        "request_type": "address_for_booking",
        "fields": {
            "booking_tool": "travel_book_taxi",
            "trip_date": "2026-10-15",
            "pickup_time": "09:00",
            "booking_summary": "Taxi to Exeter St Davids for the 09:30 train",
        },
    }
    taxi = {"date": "2026-10-15", "pickup_time": "09:00", "address": "[ADDR-1]"}

    async def scenario() -> tuple[Any, ...]:
        async with running(tmp_path, clock=story_clock()) as (_, ctx, _):
            await taint(ctx)
            held = await call(ctx, "work", "ask", ask)
            hold = ctx.holds.get(held.structured_content["hold_id"])
            assert hold is not None and hold.kind == "address"
            await ctx.decide(hold.hold_id, "taxi_only", hold.fingerprint, "test")
            status = await call(ctx, "work", "hold_status", {"hold_id": hold.hold_id})
            booked = await call(ctx, "work", "travel_book_taxi", taxi)
            second = await call(ctx, "work", "travel_book_taxi", taxi)
            email = await call(
                ctx,
                "work",
                "mail_send_message",
                {"to": "x@y.example", "subject": "address", "body": "it is [ADDR-1]"},
            )
            listing = await call(ctx, "work", "travel_list_bookings", {})
            return held, status, booked, second, email, listing, ctx

    held, status, booked, second, email, listing, ctx = asyncio.run(scenario())
    assert held.structured_content["rule"] == "address_needs_owner"
    assert status.structured_content["result"]["address"] == "[ADDR-1]"
    # The agent is told nothing is booked yet (Hermes once read "done" as "booked").
    assert "Nothing is booked yet" in status.structured_content["message"]
    assert not booked.is_error
    (booking,) = ctx.world.bookings.values()
    assert booking["from"].startswith("27 Ashdown Road")
    # The work agent sees the stand-in, never the address, in the booking and in the listing.
    for answer in (booked, listing):
        assert not find_planted(answer.model_dump_json(), side="personal")
        assert "[ADDR-1]" in answer.model_dump_json()
    assert second.structured_content["rule"] == "stand_in_used"
    assert email.structured_content["rule"] == "stand_in_misuse"
    (covered,) = [e for e in ctx.store.events(kind="tool.call") if e.payload.get("covered_by")]
    assert covered.payload["rule"] == "stand_in_covered"


def test_stale_write_is_held_with_both_versions(tmp_path: Path) -> None:
    async def scenario() -> tuple[Any, ...]:
        async with running(tmp_path, clock=story_clock(0, "00:50")) as (_, ctx, _):
            first = await call(
                ctx,
                "work",
                "todo_update_item",
                {"id": "todo-expenses", "fields": {"done": True}, "expected_version": 1},
            )
            stale = await call(
                ctx,
                "personal",
                "todo_update_item",
                {
                    "id": "todo-expenses",
                    "fields": {"title": "Submit September expenses (receipts in the blue folder)"},
                    "expected_version": 1,
                },
            )
            hold = ctx.holds.get(stale.structured_content["hold_id"])
            assert hold is not None
            decided = await ctx.decide(hold.hold_id, "approve", hold.fingerprint, "test")
            return first, stale, hold, decided, ctx

    first, stale, hold, decided, ctx = asyncio.run(scenario())
    assert first.structured_content["item"]["version"] == 2
    assert stale.structured_content["rule"] == "stale_write"
    assert hold.details["current"]["version"] == 2 and hold.details["proposed"]["read_version"] == 1
    assert [c["label"] for c in hold.choices] == ["Use this write", "Keep current version"]
    assert decided.state == "done"
    item = ctx.world.todo_item("todo-expenses")
    assert item["version"] == 3 and item["done"] is True and "blue folder" in item["title"]


def tiny_budget(policy: Policy) -> Policy:
    agents = dict(policy.agents)
    agents["work"] = agents["work"].model_copy(update={"budget_usd_per_day": Decimal("0.001")})
    return policy.model_copy(update={"agents": agents})


def test_calls_over_the_daily_budget_are_refused(tmp_path: Path) -> None:
    with http_app(tmp_path, policy=tiny_budget(fast_policy())) as (client, _, ctx):
        first = client.post("/v1/chat/completions", headers=bearer(WORK), json={"messages": []})
        second = client.post("/v1/chat/completions", headers=bearer(WORK), json={"messages": []})
        refused = ctx.store.events(kind="model.budget_refused")
    assert first.status_code == 200
    assert second.status_code == 429 and second.json()["error"]["type"] == "budget_exceeded"
    assert refused[0].payload["budget_usd"] == "0.001"


def test_blood_test_reaches_the_work_agent_as_a_typed_notice(tmp_path: Path) -> None:
    told: list[str] = []

    async def work_agent(ctx: AdminContext, key: str, message: str) -> None:
        told.append(message)

    async def scenario() -> list[Any]:
        clock = story_clock()
        async with running(tmp_path, chatty_personal, clock=clock, work=work_agent) as (_, ctx, _):
            answer = await call(
                ctx,
                "work",
                "ask",
                {"contact": "personal", "request_type": "availability", "fields": TRIP},
            )
            assert answer.structured_content["status"] == "answered"
            clock.set(clock.now() + dt.timedelta(hours=4, minutes=30), rate=0)
            await ctx.apply_world_change("chg-blood-test-added")
            for _ in range(20):
                await asyncio.sleep(0.01)
            return ctx.store.events(kind="a2a.notice")

    notices = asyncio.run(scenario())
    assert notices[0].payload["notices"] == [
        {
            "date": "2026-10-15",
            "start": "09:00",
            "end": "10:30",
            "status": "busy",
            "can_move": False,
            "change": "now_busy",
        }
    ]
    assert len(told) == 1 and "Thu 15 Oct, 09:00 to 10:30, now busy, can't move" in told[0]
    assert not find_planted(told[0], side="personal")


def test_a_decided_paid_change_is_told_to_the_agent(tmp_path: Path) -> None:
    told: list[str] = []

    async def work_agent(ctx: AdminContext, key: str, message: str) -> None:
        told.append(message)

    async def scenario() -> str:
        async with running(tmp_path, clock=story_clock(), work=work_agent) as (_, ctx, _):
            ctx.world.book_train("train-0930")
            held = await call(
                ctx,
                "work",
                "travel_change_booking",
                {"booking_id": "bk-1", "option_id": "train-1130"},
            )
            hold = ctx.holds.get(held.structured_content["hold_id"])
            assert hold is not None and hold.rule == "paid_change_needs_owner"
            await ctx.decide(hold.hold_id, "approve", hold.fingerprint, "test")
            for _ in range(20):
                await asyncio.sleep(0.01)
            return str(ctx.world.bookings["bk-1"]["depart"])

    assert asyncio.run(scenario()) == "11:30"
    assert len(told) == 1 and "was approved, and Steward made the change once" in told[0]


def test_holds_expire_on_the_clock(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, int]:
        clock = story_clock()
        async with running(tmp_path, clock=clock) as (_, ctx, _):
            held = await call(ctx, "work", "todo_delete_item", {"id": "todo-pack"})
            hold = ctx.holds.get(held.structured_content["hold_id"])
            assert hold is not None
            clock.set(clock.now() + dt.timedelta(hours=13), rate=0)
            status = await call(ctx, "work", "hold_status", {"hold_id": hold.hold_id})
            code = 0
            try:
                await ctx.decide(hold.hold_id, "approve", hold.fingerprint, "test")
            except HoldError as exc:
                code = exc.status
            return str(status.structured_content["status"]), code

    assert asyncio.run(scenario()) == ("expired", 410)


def test_the_console_approves_with_the_fingerprint(tmp_path: Path) -> None:
    with http_app(tmp_path) as (client, _, ctx):
        owner = bearer(OWNER)

        async def hold_a_send() -> str:
            held = await call(
                ctx, "work", "mail_send_message", {"to": "a@b.example", "subject": "s", "body": "b"}
            )
            return str(held.structured_content["hold_id"])

        hold_id = client.portal.call(hold_a_send) if hasattr(client, "portal") else None
        if hold_id is None:
            pytest.skip("TestClient portal not available")
        queue = client.get("/admin/holds", headers=owner).json()
        assert [h["hold_id"] for h in queue] == [hold_id]
        wrong = client.post(
            f"/admin/holds/{hold_id}/decide",
            json={"choice": "approve", "fingerprint": "x"},
            headers=owner,
        )
        assert wrong.status_code == 409
        fingerprint = queue[0]["fingerprint"]
        right = client.post(
            f"/admin/holds/{hold_id}/decide",
            json={"choice": "deny", "fingerprint": fingerprint},
            headers=owner,
        )
        assert right.status_code == 200 and right.json()["state"] == "denied"
        assert client.get("/admin/holds", headers=owner).json() == []


def test_an_old_hold_expires_into_its_own_session(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str, list[str]]:
        clock = story_clock()
        async with running(tmp_path, clock=clock) as (_, ctx, _):
            first = ctx.recorder.session_id
            held = await call(ctx, "work", "todo_delete_item", {"id": "todo-pack"})
            ctx.recorder.new_session("later run", scoped=True)
            clock.set(clock.now() + dt.timedelta(hours=13), rate=0)
            ctx.holds.pending()
            expired = ctx.store.events(kind="hold.expired")
            return first, str(held.structured_content["hold_id"]), [e.session_id for e in expired]

    first, _, sessions = asyncio.run(scenario())
    # Replay of the later session never meets a hold it did not create.
    assert sessions == [first]
