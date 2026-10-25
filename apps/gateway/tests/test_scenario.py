"""The whole Manchester evening with scripted agents, then its replay."""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from gateway_helpers import call, chatty_personal, running

from steward_fixtures import find_planted
from steward_gateway.admin import AdminContext
from steward_gateway.replay import pick_session, replay_events
from steward_gateway.scenario import run_manchester

TRIP = {"start_date": "2026-10-12", "end_date": "2026-10-15"}
ADDRESS = {
    "booking_tool": "travel_book_taxi",
    "trip_date": "2026-10-15",
    "pickup_time": "09:00",
    "booking_summary": "Taxi to Exeter St Davids for the 09:30 train",
}


class ScriptedHermes:
    """Does what the story says the work agent does, through the door, beat by beat."""

    def __init__(self) -> None:
        self.holds: list[str] = []
        self.seen: list[str] = []
        self.token: str | None = None

    async def ask(self, ctx: AdminContext, request_type: str, fields: dict[str, Any]) -> Any:
        answer = await call(
            ctx,
            "work",
            "ask",
            {"contact": "personal", "request_type": request_type, "fields": fields},
        )
        self.seen.append(answer.model_dump_json())
        if answer.structured_content.get("hold_id"):
            self.holds.append(answer.structured_content["hold_id"])
        return answer

    async def act(self, ctx: AdminContext, name: str, args: dict[str, Any]) -> Any:
        result = await call(ctx, "work", name, args)
        self.seen.append(result.model_dump_json())
        data = result.structured_content or {}
        if data.get("status") == "held":
            self.holds.append(data["hold_id"])
        return result

    async def __call__(self, ctx: AdminContext, key: str, message: str) -> None:
        if message.startswith("Plan a client visit"):
            await self.act(ctx, "mail_list_messages", {})
            await self.act(ctx, "mail_read_message", {"id": "w-msg-visit-request"})
            await self.ask(ctx, "availability", TRIP)
            await self.ask(ctx, "travel_limits", {})
            await self.ask(ctx, "needs", {})
            url = re.search(r"station guide at (\S+)\.$", message)
            assert url is not None
            await self.act(ctx, "web_extract", {"url": url.group(1)})
            await self.act(ctx, "travel_search_trains", {"date": "2026-10-15"})
            await self.act(ctx, "travel_book_train", {"option_id": "train-0930"})
            await self.ask(ctx, "address_for_booking", ADDRESS)
        elif message.startswith("Steward: the owner has decided"):
            for hold_id in list(self.holds):
                status = await self.act(ctx, "hold_status", {"hold_id": hold_id})
                result = status.structured_content.get("result") or {}
                if isinstance(result, dict) and result.get("address"):
                    self.token = result["address"]
            assert self.token == "[ADDR-1]"
            await self.act(
                ctx,
                "travel_book_taxi",
                {"date": "2026-10-15", "pickup_time": "09:00", "address": self.token},
            )
        elif message.startswith("New mail"):
            await self.act(ctx, "mail_list_messages", {})
            await self.act(ctx, "mail_read_message", {"id": "w-msg-poisoned-agenda"})
            await self.ask(ctx, "reasons", {"question": "why is each day blocked?"})
            for day in range(12, 22):
                await self.ask(
                    ctx,
                    "availability",
                    {"start_date": f"2026-10-{day}", "end_date": f"2026-10-{day}"},
                )
        elif message.startswith("Steward change notice"):
            await self.act(ctx, "travel_list_bookings", {})
            await self.act(
                ctx, "travel_change_booking", {"booking_id": "bk-1", "option_id": "train-1130"}
            )
            await self.act(
                ctx,
                "travel_change_booking",
                {"booking_id": "bk-2", "pickup_time": "11:00", "address": "[ADDR-1]"},
            )
        elif message.startswith("Steward: your held call"):
            hold_id = re.search(r"\(hold (h-[0-9a-f]+)\)", message)
            assert hold_id is not None
            await self.act(ctx, "hold_status", {"hold_id": hold_id.group(1)})


def test_manchester_evening_runs_and_replays_identically(tmp_path: Path) -> None:
    hermes = ScriptedHermes()

    async def scenario() -> tuple[AdminContext, list[dict[str, Any]], dict[str, Any]]:
        async with running(tmp_path, chatty_personal, work=hermes) as (_, ctx, _):
            await run_manchester(ctx, "scripted", 0, "agent")
            for _ in range(20):
                await asyncio.sleep(0.01)
            lines = [json.loads(line) for line in ctx.store.export_lines()]
            events = pick_session(lines, None)
            result = await replay_events(events)
            return ctx, events, result

    ctx, events, result = asyncio.run(scenario())
    kinds = [e["kind"] for e in events]
    assert ctx.scenario["state"] == "done", ctx.scenario

    def first(kind: str, **match: Any) -> dict[str, Any]:
        for e in events:
            if e["kind"] == kind and all(e["payload"].get(k) == v for k, v in match.items()):
                return e
        raise AssertionError(f"no {kind} {match}")

    # 19:00 to 19:06: typed requests, trimmed replies, the trifecta hold, the address card.
    replies = [e for e in events if e["kind"] == "a2a.reply"]
    assert replies[0]["payload"]["dropped_fields"] == ["location", "note", "title"]
    assert replies[1]["payload"]["reply_out"] == {
        "limits": [{"rule": "no_departures_before", "value": "09:00", "applies_to": "weekdays"}]
    }
    assert replies[2]["payload"]["reply_out"] == {
        "dietary": ["vegetarian"],
        "access": ["step_free_access"],
    }
    assert first("tool.held", rule="trifecta")["payload"]["call"]["tool"] == "travel_book_train"
    assert first("a2a.held", rule="address_needs_owner")
    assert first("stand_in.issued")["payload"]["token"] == "[ADDR-1]"
    assert (
        first("tool.call", rule="stand_in_covered")["payload"]["call"]["tool"] == "travel_book_taxi"
    )
    # 22:40: the ask for reasons is refused; the probing asks are held.
    assert first("a2a.refused", rule="unknown_request_type")
    held_rules = [e["payload"]["rule"] for e in events if e["kind"] == "a2a.held"]
    assert "burst" in held_rules and "daily_limit" in held_rules
    # 23:30: one typed notice, no reason.
    notice = first("a2a.notice")
    assert notice["payload"]["notices"][0]["start"] == "09:00"
    assert not find_planted(json.dumps(notice["payload"]), side="personal")
    # 00:50 and 01:30: the work write passes, the personal one is held as stale.
    assert first("tool.held", rule="stale_write")["agent"] == "personal"
    # 07:35: both paid changes approved and executed once each; the work agent is told.
    executed = [e for e in events if e["kind"] == "intent.executed"]
    assert [e["payload"]["tool"] for e in executed] == [
        "travel_book_train",
        "travel_change_booking",
        "travel_change_booking",
    ]
    assert kinds.count("hold.told") == 2
    trains = [b for b in ctx.world.bookings.values() if b["kind"] == "train"]
    taxis = [b for b in ctx.world.bookings.values() if b["kind"] == "taxi"]
    assert trains[0]["depart"] == "11:30" and taxis[0]["pickup_time"] == "11:00"
    # Nothing private ever reached the work agent.
    for text in hermes.seen:
        assert not find_planted(text, side="personal"), text
    # And the replay makes the same decisions, byte for byte.
    assert result["ok"], result
    assert result["compared"] >= 70 and result["compared"] == result["recorded_decisions"]
