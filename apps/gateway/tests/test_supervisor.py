"""The scheduler on Steward's clock and the morning brief's writer (Phase 3, Parts B and E)."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from gateway_helpers import make_settings, running, story_clock

from steward_core.clock import StewardClock
from steward_fixtures import at, resolve_anchor
from steward_gateway.app import create_app
from steward_gateway.supervisor import latest_brief


def story(day: int, hhmm: str) -> Any:
    return at(resolve_anchor("replay"), day, hhmm)


def test_the_brief_runs_once_when_story_time_passes_0730(tmp_path: Path) -> None:
    async def scenario() -> list[list[str]]:
        clock = story_clock(0, "07:29")
        async with running(tmp_path, clock=clock) as (_, ctx, _):
            ctx.recorder.new_session("story", scoped=True)
            assert ctx.scheduler is not None
            ctx.scheduler.observe()
            ran = [await ctx.scheduler.tick()]
            clock.set(story(0, "07:31"), rate=0)
            ran += [await ctx.scheduler.tick(), await ctx.scheduler.tick()]
            assert len(ctx.store.events(kind="brief.written", limit=10)) == 1
            return ran

    assert asyncio.run(scenario()) == [[], ["brief"], []]


def test_a_brief_missed_while_steward_was_down_runs_once_after_the_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[list[str], list[str], int]:
        clock = story_clock(0, "07:29")
        async with running(tmp_path, clock=clock) as (_, ctx, _):
            assert ctx.scheduler is not None
            await ctx.scheduler.tick()
            clock.set(story(0, "07:31"), rate=0)
            assert await ctx.scheduler.tick() == ["brief"]
        # Down over the next morning's 07:30; back at 08:00.
        later = StewardClock()
        later.set(story(1, "08:00"), rate=0)
        async with running(tmp_path, clock=later) as (_, ctx, _):
            assert ctx.scheduler is not None
            first = await ctx.scheduler.tick()
            second = await ctx.scheduler.tick()
            briefs = len(ctx.store.events(kind="brief.written", limit=10))
        return first, second, briefs

    assert asyncio.run(scenario()) == (["brief"], [], 2)


def brief_with(tmp_path: Path, answer: Callable[[httpx.Request], httpx.Response]) -> dict[str, Any]:
    """Run the brief once with the helper model answering through `answer`."""
    sent: list[dict[str, Any]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return answer(request)

    async def scenario() -> dict[str, Any]:
        clock = story_clock(0, "07:30")
        app = create_app(
            make_settings(tmp_path),
            transport=httpx.MockTransport(upstream),
            adapters={},
            clock=clock,
        )
        ctx = app.state.steward
        async with app.router.lifespan_context(app):
            await ctx.scheduler.run_job("brief", clock.now())
            found = latest_brief(ctx)
            assert found is not None
            calls = ctx.store.events(kind="helper.call", limit=5)
            found["helper_calls"] = [e.payload for e in calls]
            return found

    found = asyncio.run(scenario())
    found["sent"] = sent
    return found


def completion(text: str) -> Callable[[httpx.Request], httpx.Response]:
    usage = {"prompt_tokens": 900, "completion_tokens": 40, "total_tokens": 940}
    body = {"choices": [{"message": {"role": "assistant", "content": text}}], "usage": usage}
    return lambda request: httpx.Response(200, json=body)


def test_the_models_lines_are_used_when_they_pass_the_check(tmp_path: Path) -> None:
    found = brief_with(tmp_path, completion("Watcher: no agent hit a hard limit."))
    assert found["source"] == "model" and found["model"] == "nvidia/Nemotron-3_5-Lightning"
    assert found["text"].startswith("Watcher: no agent hit a hard limit.")
    assert found["text"].endswith("No question this morning.")
    body = found["sent"][0]
    assert body["model"] == "nvidia/Nemotron-3_5-Lightning"
    assert body["reasoning_effort"] == "none"
    # The writer sees typed facts only: no ids, no fingerprints, no question.
    facts = json.loads(body["messages"][1]["content"])
    assert "brief_id" not in facts and "question" not in facts
    assert found["helper_calls"][0]["cost_usd"] is not None


def test_an_invented_time_falls_back_to_the_template(tmp_path: Path) -> None:
    found = brief_with(tmp_path, completion("Work agent: the train leaves at 06:45."))
    assert found["source"] == "template"
    assert any("06:45" in p for p in found["problems"])
    assert "06:45" not in found["text"]


def test_a_failed_model_call_falls_back_to_the_template(tmp_path: Path) -> None:
    found = brief_with(tmp_path, lambda request: httpx.Response(500))
    assert found["source"] == "template" and found["problems"] == ["HTTP 500"]
    assert "Watcher: no agent hit a hard limit." in found["text"]
