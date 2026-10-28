"""Agent-level replay (P2-D7 option A): play a recorded session back through a fresh Steward.

The flight log holds every action that crossed a door: each agent call (one event carries
{"call": ...}), each owner decision ("owner_action"), each change to the fixture world
("world_action"), and the outcomes that came from outside Steward (tool results, the end of an
agent's run, a timeout, a model call's usage). Replay builds Steward in memory under the
recorded session id (so it mints the same ids), sets the clock to each recorded instant, and
feeds those actions back in order. Mock adapters stand in for the agents, recorded results stand
in for Tavily, and Steward's checks run fresh. No sandbox, no model call, no key.

The decision events (verdicts, trimmed replies, holds, executions) of the replay are then
compared with the recording, as canonical JSON, after dropping timings. A pass shows Steward's
decisions are deterministic; it does not show that the agents would act the same way again.

Phase 3: scheduled jobs are replayed from their scheduler.run events ("system_action"), the
morning brief's facts are recomputed and compared, and its text comes from the recording (like
web results), so replay never calls the helper model. The watcher is not replayed from the log:
it runs again on the replayed events and must pause at the same event.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import httpx

from steward_core.canonical import jcs
from steward_core.clock import StewardClock, parse_iso
from steward_core.policy import Policy, load_policy
from steward_core.store import Store
from steward_fixtures import World, resolve_anchor
from steward_gateway.adapters import DeliveryResult
from steward_gateway.holds import HoldError
from steward_gateway.runtime import Runtime
from steward_gateway.settings import Settings

DECISION_KINDS = frozenset(
    {
        "tool.call",
        "tool.denied",
        "tool.refused",
        "tool.held",
        "tool.result",
        "hold.status",
        "a2a.request",
        "a2a.refused",
        "a2a.held",
        "a2a.reply",
        "a2a.reply_invalid",
        "a2a.reply_rejected",
        "a2a.timeout",
        "a2a.no_reply",
        "a2a.notice",
        "stand_in.issued",
        "hold.approved",
        "hold.denied",
        "hold.rejected",
        "hold.expired",
        "intent.executed",
        "intent.duplicate",
        "flags.cleared",
        "model.call",
        "model.budget_refused",
        "world.change",
        "world.reset",
        "admin.pass_suspended",
        "admin.pass_resumed",
        # Phase 3
        "admin.pass_revoked",
        "admin.tool_off",
        "admin.tool_on",
        "watcher.paused",
        "mail.screened",
        "mail.verdict",
        "scheduler.run",
        "brief.facts",
    }
)
# Timings differ between runs; everything else in a decision event must match.
VOLATILE = frozenset({"latency_ms", "duration_ms"})
# Outcomes from outside Steward: the replayer releases the waiting code with the recording.
GATED = {
    "tool.result": "call_id",
    "intent.executed": "intent_key",
    "brief.written": "brief_id",
}
DELIVERIES = {"a2a.delivery": "request_id", "a2a.notice_delivery": "notice_id", "hold.told": "key"}
WAIT_S = 5.0


class ReplayGate:
    """Holds back an outcome until the replay reaches the event that recorded it."""

    def __init__(self) -> None:
        self._waiting: dict[tuple[str, str], asyncio.Future[dict[str, Any] | None]] = {}
        self._ready: dict[tuple[str, str], dict[str, Any]] = {}

    async def wait(self, kind: str, key: str) -> dict[str, Any] | None:
        if (kind, key) in self._ready:
            return self._ready.pop((kind, key))
        future: asyncio.Future[dict[str, Any] | None] = asyncio.get_running_loop().create_future()
        self._waiting[(kind, key)] = future
        return await future

    def release(self, kind: str, key: str, payload: dict[str, Any]) -> None:
        future = self._waiting.pop((kind, key), None)
        if future is not None and not future.done():
            future.set_result(payload)
        else:
            self._ready[(kind, key)] = payload


class ReplayAdapter:
    """Stands in for an agent's run: it ends when the recording says the run ended."""

    def __init__(self, gate: ReplayGate) -> None:
        self.gate = gate

    async def deliver(self, request_id: str, message: str) -> DeliveryResult:
        recorded = await self.gate.wait("delivery", request_id) or {}
        return DeliveryResult(
            ok=bool(recorded.get("ok")),
            status=str(recorded.get("status")),
            duration_ms=int(recorded.get("duration_ms") or 0),
            exit_code=recorded.get("exit_code"),
            usage=recorded.get("usage"),
            tools=list(recorded.get("tools") or []),
            final=recorded.get("final"),
            error=recorded.get("error"),
        )


def decision(event: Mapping[str, Any]) -> bytes:
    payload = {k: v for k, v in dict(event["payload"]).items() if k not in VOLATILE}
    return jcs({"kind": event["kind"], "agent": event.get("agent"), "payload": payload})


def load_recording(path: Path | str) -> list[dict[str, Any]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def pick_session(
    events: Iterable[Mapping[str, Any]], session_id: str | None
) -> list[dict[str, Any]]:
    """The events of one session: the given one, else the last scenario run in the recording."""
    rows = [dict(e) for e in events]
    if session_id is None:
        starts = [e for e in rows if e["kind"] == "scenario.start"]
        if not starts:
            raise ValueError("no scenario run in the recording; pass a session id")
        session_id = str(starts[-1]["session_id"])
    chosen = [e for e in rows if e["session_id"] == session_id]
    if not chosen:
        raise ValueError(f"no events for session {session_id}")
    return sorted(chosen, key=lambda e: int(e["seq"]))


def _replay_settings(policy: Policy) -> Settings:
    return Settings(
        nebius_api_key="replay-no-key",
        owner_token="replay-owner",
        passes={agent: f"replay-pass-{agent}" for agent in policy.agents},
        policy=policy,
        db_path=Path(":memory:"),
        console_dir=Path("replay-no-console"),
        web_backend="fixture",
    )


async def replay_events(
    events: list[dict[str, Any]], policy_path: str | None = None
) -> dict[str, Any]:
    from steward_gateway.app import create_app

    started = time.perf_counter()
    session_id = str(events[0]["session_id"])
    start = next((e for e in events if e["kind"] == "scenario.start"), None)
    if start is not None and start["payload"].get("policy"):
        policy = Policy.model_validate(start["payload"]["policy"])
    else:
        policy = load_policy(policy_path or "config/steward.yaml")
    anchor = str(start["payload"].get("anchor")) if start else None
    scoped = any(
        e["kind"] == "system.session" and e["payload"].get("scope") == "session" for e in events
    )

    clock = StewardClock()
    clock.set(parse_iso(str(events[0]["ts"])), rate=0)
    store = Store(":memory:")
    gate = ReplayGate()
    adapters = {agent: ReplayAdapter(gate) for agent in policy.agents}
    world = World(resolve_anchor(anchor or "replay"), clock=clock.now)
    # Asks wait as long as the replay needs; recorded timeouts are applied explicitly.
    policy = policy.model_copy(
        update={"a2a": policy.a2a.model_copy(update={"reply_timeout_s": 3600, "reply_grace_s": 0})}
    )
    app = create_app(
        _replay_settings(policy),
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        adapters=adapters,
        store=store,
        clock=clock,
        world=world,
        gate=gate,
        session_id=session_id,
        anchor_value=anchor or "replay",
    )
    runtime: Runtime = app.state.steward
    if scoped:
        runtime.recorder.scope = session_id
    tasks: set[asyncio.Task[Any]] = set()
    recorded_counts: Counter[str] = Counter()

    def produced(kind: str) -> int:
        return sum(
            1
            for e in store.events(kind=kind, limit=5000, newest_first=False)
            if e.session_id == session_id
        )

    async def catch_up(kind: str) -> bool:
        """Wait until the replay has produced this event; False if it never does."""
        deadline = time.monotonic() + WAIT_S
        while produced(kind) < recorded_counts[kind] and time.monotonic() < deadline:
            await asyncio.sleep(0.001)
        for _ in range(5):
            await asyncio.sleep(0.001)
        return produced(kind) >= recorded_counts[kind]

    want = [e for e in events if e["kind"] in DECISION_KINDS]
    checked = 0

    def diverged() -> bool:
        """Compare the replay's new decision events with the recording, as they appear."""
        nonlocal checked
        got = [
            e.as_dict()
            for e in store.events(after_seq=0, limit=5000, newest_first=False)
            if e.session_id == session_id and e.kind in DECISION_KINDS
        ]
        while checked < len(got):
            if checked >= len(want) or decision(want[checked]) != decision(got[checked]):
                return True
            checked += 1
        return False

    def spawn(coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def owner(action: Mapping[str, Any], agent: str | None) -> None:
        name = action.get("action")
        if name == "decide":
            try:
                await runtime.decide(
                    str(action["hold_id"]),
                    str(action["choice"]),
                    str(action["fingerprint"]),
                    str(action.get("by", "owner")),
                )
            except HoldError:
                pass
        elif name == "clear_flags" and agent:
            runtime.clear_flags(agent, str(action.get("by", "owner")))
        elif name in {"suspend", "resume"} and agent:
            try:
                runtime.set_suspended(agent, name == "suspend")
            except ValueError:
                pass
        elif name == "revoke" and agent:
            runtime.revoke(agent, str(action.get("by", "owner")))
        elif name in {"tool_off", "tool_on"} and agent:
            try:
                runtime.set_tool(
                    agent, str(action["tool"]), name == "tool_on", str(action.get("by", "owner"))
                )
            except ValueError:
                pass
        elif name == "mail_verdict":
            runtime.mail_verdict(
                str(action["message_id"]), str(action["verdict"]), str(action.get("by", "owner"))
            )

    async with app.router.lifespan_context(app):
        for event in events:
            kind = str(event["kind"])
            payload = dict(event["payload"])
            agent = event.get("agent")
            clock.set(parse_iso(str(event["ts"])), rate=0)
            if kind in DECISION_KINDS:
                recorded_counts[kind] += 1
            if "call" in payload and agent:
                call = payload["call"]
                spawn(
                    runtime.tool_door.call(str(agent), str(call["tool"]), dict(call["arguments"]))
                )
            elif "owner_action" in payload:
                spawn(owner(payload["owner_action"], agent))
            elif "world_action" in payload:
                action = payload["world_action"]
                if action.get("reset"):
                    runtime.reset_world(follow_clock=bool(action.get("follow_clock")))
                elif action.get("change"):
                    await runtime.apply_world_change(str(action["change"]))
            elif "system_action" in payload and runtime.scheduler is not None:
                action = payload["system_action"]
                spawn(runtime.scheduler.run_job(str(action["job"]), parse_iso(str(action["due"]))))
            elif kind in GATED:
                gate.release(kind, str(payload[GATED[kind]]), payload)
            elif kind in DELIVERIES:
                gate.release("delivery", str(payload.get(DELIVERIES[kind])), payload)
            elif kind == "a2a.timeout":
                runtime.a2a.force_timeout(str(payload["request_id"]))
            elif kind == "hold.expired":
                # Expiry is noticed whenever a hold is read (an agent, the console, the runner).
                # Reading it at the recorded moment notices it the same way.
                runtime.holds.get(str(payload["hold_id"]))
            elif kind in {"model.call", "model.budget_refused"} and agent:
                runtime.model_door.replay_call(str(agent), payload)
            else:
                continue
            if kind in DECISION_KINDS:
                caught_up = await catch_up(kind)
            else:
                caught_up = True
                for _ in range(5):
                    await asyncio.sleep(0.001)
            # Stop at the first difference: everything after it would differ too.
            if not caught_up or diverged():
                break
        for _ in range(50):
            await asyncio.sleep(0.002)
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    got = [
        e.as_dict()
        for e in store.events(limit=5000, newest_first=False)
        if e.session_id == session_id and e.kind in DECISION_KINDS
    ]
    first: dict[str, Any] | None = None
    for index in range(max(len(want), len(got))):
        a = want[index] if index < len(want) else None
        b = got[index] if index < len(got) else None
        if a is None or b is None or decision(a) != decision(b):
            first = {
                "index": index,
                "recorded": _brief(a),
                "replayed": _brief(b),
            }
            break
    return {
        "ok": first is None,
        "session_id": session_id,
        "compared": min(len(want), len(got)),
        "recorded_decisions": len(want),
        "replayed_decisions": len(got),
        "events_in_recording": len(events),
        "first_difference": first,
        "seconds": round(time.perf_counter() - started, 2),
    }


def _brief(event: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    payload = {k: v for k, v in dict(event["payload"]).items() if k not in VOLATILE}
    return {
        "seq": event.get("seq"),
        "kind": event["kind"],
        "agent": event.get("agent"),
        "payload": payload,
    }


async def replay_store(runtime: Runtime, session_id: str | None = None) -> dict[str, Any]:
    """Replay a session from this Steward's own flight log (the console's Replay button)."""
    lines = [json.loads(line) for line in runtime.store.export_lines()]
    try:
        events = pick_session(lines, session_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    result = await replay_events(events)
    return result
