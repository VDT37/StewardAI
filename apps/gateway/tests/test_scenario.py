"""The whole Manchester evening with scripted agents, then its replay."""

import asyncio
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

import pytest
from gateway_helpers import ScriptedHermes, call, chatty_personal, running, story_clock

from steward_fixtures import find_planted
from steward_gateway.admin import AdminContext
from steward_gateway.prompted import prompted_by
from steward_gateway.replay import pick_session, replay_events
from steward_gateway.scenario import Runner, run_manchester
from steward_gateway.tool_door import Catalogue


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
    # 21:15 and 22:40: both attack mails are screened; the phish is reported and its link was
    # never shown, and the 22:40 hidden instruction never reached the agent.
    screened = {e["payload"]["message_id"] for e in events if e["kind"] == "mail.screened"}
    assert screened == {"w-msg-phishing-verify", "w-msg-poisoned-agenda"}
    assert "w-msg-phishing-verify" in ctx.world.reported
    assert not any("mailbox-verify.example" in text for text in hermes.seen)
    assert not any("ASSISTANT INSTRUCTION" in text for text in hermes.seen)
    # 07:30: the brief nobody asked for, from code-picked facts, with one question about the
    # trip's two paid changes; the story never trips a hard limit.
    assert first("scheduler.run", job="brief")
    facts = first("brief.facts")["payload"]["facts"]
    assert facts["question"]["kind"] == "change_paid" and len(facts["question"]["holds"]) == 2
    assert not find_planted(json.dumps(facts), side="personal")
    assert first("brief.written")["payload"]["source"] in {"model", "template"}
    assert "watcher.paused" not in kinds

    # Each held call's card says what started the agent's run, from Steward's own records.
    def prompted(rule: str) -> str:
        hold = next(h for h in ctx.store.holds(state=None) if h.rule == rule)
        return prompted_by(ctx, hold) or ""

    assert prompted("trifecta").startswith('Your request at 19:00: "Plan a client visit')
    assert prompted("paid_change_needs_owner").startswith(
        "Steward's change notice at 23:30 (Thu 15 Oct, 09:00 to 10:30, now busy)"
    )
    # attacks="agent": the scripted Hermes falls for the 22:40 email in the run "New mail" started.
    assert prompted("daily_limit").startswith('Your request at 22:40: "New mail')
    assert prompted("stale_write").startswith("The overnight script at 01:30")
    # Nothing private ever reached the work agent.
    for text in hermes.seen:
        assert not find_planted(text, side="personal"), text
    # And the replay makes the same decisions, byte for byte.
    assert result["ok"], result
    assert result["compared"] >= 70 and result["compared"] == result["recorded_decisions"]


def test_owner_first_takes_the_scripted_decision_once_the_owner_had_their_chance(
    tmp_path: Path,
) -> None:
    """approvals="owner-first": nobody decides within the wait, so the scripted decisions run,
    the evening ends as the scripted one does, and it still replays identically."""
    hermes = ScriptedHermes()

    async def scenario() -> tuple[AdminContext, list[dict[str, Any]], dict[str, Any]]:
        async with running(tmp_path, chatty_personal, work=hermes) as (_, ctx, _):
            await run_manchester(ctx, "owner-first", 0, "agent", owner_wait_s=0.05)
            for _ in range(20):
                await asyncio.sleep(0.01)
            lines = [json.loads(line) for line in ctx.store.export_lines()]
            events = pick_session(lines, None)
            return ctx, events, await replay_events(events)

    ctx, events, result = asyncio.run(scenario())
    assert ctx.scenario["state"] == "done", ctx.scenario
    waits = [e for e in events if e["payload"].get("mode") == "owner-first"]
    assert [e["payload"]["beat"] for e in waits] == ["19:06 approvals", "07:35 approvals"]
    executed = [e["payload"]["tool"] for e in events if e["kind"] == "intent.executed"]
    assert executed == ["travel_book_train", "travel_change_booking", "travel_change_booking"]
    start = next(e for e in events if e["kind"] == "scenario.start")
    assert start["payload"]["real_time"] is False
    assert result["ok"], result["first_difference"]


def test_a_real_time_run_never_cuts_a_quiet_gap_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fast-forward caps every quiet gap (MAX_GAP_S); real time waits the whole gap."""
    monkeypatch.setattr("steward_gateway.scenario.MAX_GAP_S", 0.01)

    async def gap_seconds(real_time: bool) -> tuple[float, float]:
        async with running(tmp_path / str(real_time), chatty_personal) as (_, ctx, _):
            runner = Runner(ctx, "scripted", 1.0, "agent", real_time=real_time)
            start = ctx.clock.now()
            began = time.monotonic()
            await runner.advance_to(start + dt.timedelta(seconds=0.3))
            return time.monotonic() - began, (ctx.clock.now() - start).total_seconds()

    fast_wall, fast_story = asyncio.run(gap_seconds(False))
    real_wall, real_story = asyncio.run(gap_seconds(True))
    # Both land on the beat's time; only real time spends the whole gap waiting for it.
    assert fast_story >= 0.3 and real_story >= 0.3
    assert fast_wall < 0.2
    assert real_wall >= 0.29


def test_replay_matches_even_when_upstream_calls_are_slow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every upstream call takes 5 ms here, so the live clock moves on between a decision
    and its execution. Replay must still match: decisions use the moment they were made."""
    original = Catalogue.call

    async def slow(self: Catalogue, tool_id: str, arguments: dict[str, Any]) -> Any:
        await asyncio.sleep(0.005)
        result = await original(self, tool_id, arguments)
        # The gap between the upstream write and the logged result is what a slow machine
        # widens; replay must not depend on it.
        await asyncio.sleep(0.005)
        return result

    monkeypatch.setattr(Catalogue, "call", slow)
    hermes = ScriptedHermes()

    async def scenario() -> dict[str, Any]:
        async with running(tmp_path, chatty_personal, work=hermes) as (_, ctx, _):
            await run_manchester(ctx, "scripted", 0, "agent")
            for _ in range(20):
                await asyncio.sleep(0.01)
            lines = [json.loads(line) for line in ctx.store.export_lines()]
            return await replay_events(pick_session(lines, None))

    result = asyncio.run(scenario())
    assert result["ok"], result["first_difference"]


def test_a_hold_that_expires_while_the_owner_looks_replays(tmp_path: Path) -> None:
    """Expiry noticed by a read that is not an agent's call (the console, the runner) is in the
    log; replay notices it at the same moment."""

    async def scenario() -> dict[str, Any]:
        clock = story_clock()
        async with running(tmp_path, clock=clock) as (_, ctx, _):
            ctx.recorder.new_session("expiry", scoped=True)
            await call(ctx, "work", "todo_delete_item", {"id": "todo-pack"})
            clock.set(clock.now() + dt.timedelta(hours=13), rate=0)
            assert ctx.holds.pending() == []
            assert ctx.flags.peek("work", clock.now()).as_dict()["private_read"] == []
            lines = [json.loads(line) for line in ctx.store.export_lines()]
            events = [e for e in lines if e["session_id"] == ctx.recorder.session_id]
            assert [e["kind"] for e in events][-1] == "hold.expired"
            return await replay_events(events)

    result = asyncio.run(scenario())
    assert result["ok"], result["first_difference"]
