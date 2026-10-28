"""The full ask flow, with a scripted personal agent that answers through the tool door."""

import asyncio
import re
import sys
from pathlib import Path
from typing import Any

from gateway_helpers import call, fast_policy, running, without_burst

from steward_gateway.adapters import OpenClawExecAdapter
from steward_gateway.admin import AdminContext

FIELDS = {"start_date": "2026-10-12", "end_date": "2026-10-15"}
ASK = {"contact": "personal", "request_type": "availability", "fields": FIELDS}


async def chatty_personal_agent(ctx: AdminContext, request_id: str, message: str) -> None:
    """Reads its calendar and replies with every detail, as a careless agent would."""
    span = re.search(r"from (\S+) to (\S+)\.", message)
    assert span is not None
    events = await call(
        ctx,
        "personal",
        "calendar_list_events",
        {"start_date": span.group(1), "end_date": span.group(2)},
    )
    blocks = [
        {
            "date": e["start"][:10],
            "start": e["start"][11:16],
            "end": e["end"][11:16],
            "status": "busy",
            "can_move": e["can_move"],
            "title": e["title"],
            "location": e["location"],
        }
        for e in events.structured_content["events"]
    ]
    note = "Wednesday is the hospital, cannot be rescheduled."
    accepted = await call(
        ctx,
        "personal",
        "reply",
        {"request_id": request_id, "fields": {"blocks": blocks, "note": note}},
    )
    assert accepted.structured_content == {"accepted": True}


def test_availability_crosses_trimmed(tmp_path: Path) -> None:
    async def scenario() -> tuple[Any, AdminContext, list[str]]:
        async with running(tmp_path, chatty_personal_agent) as (_, ctx, adapter):
            answer = await call(ctx, "work", "ask", ASK)
            return answer, ctx, adapter.messages

    answer, ctx, messages = asyncio.run(scenario())
    assert not answer.is_error
    data = answer.structured_content
    assert data["status"] == "answered"
    assert {
        "date": "2026-10-14",
        "start": "14:00",
        "end": "16:00",
        "status": "busy",
        "can_move": False,
    } in data["reply"]["blocks"]
    assert "Wed 14 Oct, 14:00 to 16:00, busy, can't move" in answer.content[0].text
    # School run 08:00 to 08:50 crosses as 08:00 to 09:00 on each of the four days.
    assert sum(b["start"] == "08:00" and b["end"] == "09:00" for b in data["reply"]["blocks"]) == 4
    crossed = answer.model_dump_json()
    for secret in ("Cardiology", "Dr Shah", "Riverside", "hospital", "Maya", "Priya", "14:15"):
        assert secret not in crossed
    # The message the personal agent got names its own tools and carries no asker text.
    assert "steward__calendar_list_events" in messages[0] and "steward__reply" in messages[0]

    events = ctx.store.events(limit=1000, newest_first=False)
    a2a = [e.kind for e in events if e.door == "a2a"]
    assert a2a == ["a2a.request", "a2a.reply", "a2a.delivery"]
    reply = next(e for e in events if e.kind == "a2a.reply")
    assert reply.payload["dropped_fields"] == ["location", "note", "title"]
    assert "busy_widened" in reply.payload["rules_fired"]
    assert "Dr Shah" in str(reply.payload["reply_in"])  # the owner's log keeps the full reply
    tool_calls = [e for e in events if e.kind == "tool.call" and e.agent == "personal"]
    assert tool_calls and tool_calls[0].payload["a2a_request"] == reply.payload["request_id"]
    assert ctx.store.verify().ok


def test_unknown_type_is_refused_logged_and_counted(tmp_path: Path) -> None:
    async def scenario() -> tuple[Any, AdminContext]:
        async with running(tmp_path, chatty_personal_agent) as (_, ctx, _):
            answer = await call(
                ctx,
                "work",
                "ask",
                {"contact": "personal", "request_type": "reasons", "fields": {"q": "why busy?"}},
            )
            return answer, ctx

    answer, ctx = asyncio.run(scenario())
    assert answer.is_error and answer.structured_content["status"] == "refused"
    (refused,) = ctx.store.events(kind="a2a.refused")
    assert refused.payload["rule"] == "unknown_request_type"
    assert refused.payload["asks_today"] == 1
    assert ctx.store.events(kind="a2a.request") == []


def test_eleventh_ask_of_the_day_is_held(tmp_path: Path) -> None:
    async def quiet(ctx: AdminContext, request_id: str, message: str) -> None:
        return None

    async def scenario() -> tuple[list[str], AdminContext]:
        # The same ask eleven times is also a runaway loop; the watcher has its own tests.
        policy = without_burst(fast_policy(reply_grace_s=0)).model_copy(update={"watcher": None})
        async with running(tmp_path, quiet, policy=policy) as (_, ctx, _):
            statuses = []
            for _ in range(11):
                answer = await call(ctx, "work", "ask", ASK)
                statuses.append(answer.structured_content["status"])
            return statuses, ctx

    statuses, ctx = asyncio.run(scenario())
    assert statuses == ["no_answer"] * 10 + ["held"]
    (held,) = ctx.store.events(kind="a2a.held")
    assert held.payload["rule"] == "daily_limit" and held.payload["hold_id"].startswith("h-")
    assert len(ctx.store.events(kind="a2a.no_reply", limit=50)) == 10
    (hold,) = ctx.holds.pending()
    assert hold.kind == "ask" and hold.args["request_type"] == "availability"


def test_a_burst_of_asks_is_held(tmp_path: Path) -> None:
    async def quiet(ctx: AdminContext, request_id: str, message: str) -> None:
        return None

    async def scenario() -> list[str]:
        async with running(tmp_path, quiet, policy=fast_policy(reply_grace_s=0)) as (_, ctx, _):
            statuses = []
            for _ in range(7):
                answer = await call(ctx, "work", "ask", ASK)
                statuses.append(answer.structured_content.get("rule") or "asked")
            return statuses

    assert asyncio.run(scenario()) == ["asked"] * 5 + ["burst", "burst"]


def test_no_reply_in_time_shares_nothing(tmp_path: Path) -> None:
    async def slow(ctx: AdminContext, request_id: str, message: str) -> None:
        await asyncio.sleep(1)

    async def scenario() -> tuple[Any, AdminContext]:
        async with running(tmp_path, slow, policy=fast_policy(reply_timeout_s=0.2)) as (
            _,
            ctx,
            _,
        ):
            answer = await call(ctx, "work", "ask", ASK)
            assert ctx.a2a.pending == {}
            return answer, ctx

    answer, ctx = asyncio.run(scenario())
    assert answer.structured_content["status"] == "no_answer"
    assert len(ctx.store.events(kind="a2a.timeout")) == 1


def test_bad_reply_can_be_retried_and_strangers_cannot_reply(tmp_path: Path) -> None:
    async def clumsy(ctx: AdminContext, request_id: str, message: str) -> None:
        bad = await call(
            ctx, "personal", "reply", {"request_id": request_id, "fields": {"blocks": "busy"}}
        )
        assert bad.is_error and "call reply again" in bad.content[0].text
        wrong = await call(ctx, "personal", "reply", {"request_id": "r-000000000000", "fields": {}})
        assert wrong.is_error
        block = {
            "date": "2026-10-13",
            "start": "19:00",
            "end": "21:00",
            "status": "busy",
            "can_move": True,
        }
        good = await call(
            ctx, "personal", "reply", {"request_id": request_id, "fields": {"blocks": [block]}}
        )
        assert not good.is_error
        again = await call(
            ctx, "personal", "reply", {"request_id": request_id, "fields": {"blocks": []}}
        )
        assert again.is_error

    async def scenario() -> tuple[Any, AdminContext]:
        async with running(tmp_path, clumsy) as (_, ctx, _):
            return await call(ctx, "work", "ask", ASK), ctx

    answer, ctx = asyncio.run(scenario())
    assert answer.structured_content["reply"]["blocks"] == [
        {"date": "2026-10-13", "start": "19:00", "end": "21:00", "status": "busy", "can_move": True}
    ]
    assert len(ctx.store.events(kind="a2a.reply_invalid")) == 1
    assert len(ctx.store.events(kind="a2a.reply_rejected")) == 2


def test_openclaw_adapter_parses_the_envelope(tmp_path: Path) -> None:
    fake = tmp_path / "fake_openshell.py"
    fake.write_text(
        "import json, sys\n"
        "assert sys.argv[1:6] == ['sandbox', 'exec', '-n', 'personal-agent', '--']\n"
        "assert sys.argv[6:9] == ['openclaw', 'agent', 'exec'] and '--json' in sys.argv\n"
        "print('starting...')\n"
        "print(json.dumps({'ok': True, 'status': 'ok', 'final': 'done', 'usage': {'total': 9},"
        " 'toolSummary': {'tools': ['steward__reply']}}, indent=2))\n",
        encoding="utf-8",
    )
    adapter = OpenClawExecAdapter(
        "personal-agent", timeout_s=5, command=[sys.executable, str(fake)]
    )
    result = asyncio.run(adapter.deliver("r-1", "hello"))
    assert result.ok and result.exit_code == 0 and result.tools == ["steward__reply"]
    assert result.usage == {"total": 9} and result.final == "done"

    broken = OpenClawExecAdapter("x", timeout_s=5, command=[sys.executable, "-c", "exit(2)"])
    failed = asyncio.run(broken.deliver("r-2", "hello"))
    assert not failed.ok and failed.status == "no_json" and failed.exit_code == 2
    missing = OpenClawExecAdapter("x", command=[str(tmp_path / "nope")])
    assert asyncio.run(missing.deliver("r-3", "hello")).status == "not_started"
