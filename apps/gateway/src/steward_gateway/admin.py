"""The owner's admin API, behind the owner token (Authorization: Bearer <STEWARD_OWNER_TOKEN>).

The agents' OpenShell profiles only allow /v1/** and /tools/<side>/**, so the sandboxes cannot
reach these routes even though they share the port. Until Phase 3 the owner reaches them through
an SSH tunnel (Phase 1 D7).

Phase 2 adds the approvals queue (holds, decided with their fingerprint), the trifecta flags,
Steward's clock, the fixture world, the agent-to-agent exchanges (what was sent beside what
crossed), the policy file, scenario runs and replay.

Phase 3 adds the morning brief (read it, answer its question, run it now), the contain controls
(pause, resume, revoke, a tool off or on), suspected phishing with the owner's verdict, and the
watcher. In demo mode (the public demo, Part F) there is no owner token: anyone may read, and
nothing that changes Steward is reachable.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from steward_core.clock import london_day, parse_iso
from steward_core.store import Event, Hold
from steward_gateway.holds import HoldError, short_fingerprint
from steward_gateway.prompted import prompted_by
from steward_gateway.runtime import AdminContext, Runtime

KEEPALIVE_S = 15.0
A2A_KINDS = {
    "a2a.request",
    "a2a.refused",
    "a2a.held",
    "a2a.reply",
    "a2a.reply_invalid",
    "a2a.timeout",
    "a2a.no_reply",
    "a2a.notice",
    "stand_in.issued",
}

__all__ = ["AdminContext", "admin_router"]


class SessionIn(BaseModel):
    label: str | None = None


class DecideIn(BaseModel):
    choice: str
    fingerprint: str


class ClockIn(BaseModel):
    at: datetime
    rate: float = 1.0


class WorldResetIn(BaseModel):
    follow_clock: bool = False


class WorldChangeIn(BaseModel):
    change_id: str


class ScenarioIn(BaseModel):
    name: str = "manchester"
    approvals: str = "scripted"
    speed: float = 60.0
    attacks: str = "fallback"
    real_time: bool = False
    owner_wait_min: float = 15.0
    plan: str | None = None


class ReplayIn(BaseModel):
    session_id: str | None = None


class AnswerIn(BaseModel):
    brief_id: str
    choice: str  # "approve" or "deny": the brief's question's own choice ids


class VerdictIn(BaseModel):
    verdict: str  # phishing or not_phishing


def event_json(event: Event) -> dict[str, Any]:
    return event.as_dict()


def hold_json(hold: Hold, policy_class: str | None, prompted: str | None = None) -> dict[str, Any]:
    return {
        **hold.as_dict(),
        "fingerprint_short": short_fingerprint(hold.fingerprint),
        "tool_class": policy_class,
        "prompted_by": prompted,
    }


def admin_router(ctx: Runtime) -> APIRouter:
    demo = ctx.mode == "demo"

    def owner(request: Request) -> None:
        if demo:
            # The public demo: reading is open to anyone, changing anything is not, and a
            # visitor's request never writes to the log.
            if request.method not in {"GET", "HEAD"}:
                raise HTTPException(403, "the public demo is read-only")
            return
        if not ctx.passes.is_owner(request.headers.get("authorization")):
            ctx.recorder.record(
                "auth", "auth.denied", {"path": request.url.path, "status": 401, "reason": "owner"}
            )
            raise HTTPException(401, "owner token required")

    router = APIRouter(prefix="/admin", dependencies=[Depends(owner)])
    policy = ctx.settings.policy

    def tool_class(hold: Hold) -> str | None:
        if hold.tool_id and hold.tool_id in policy.tools:
            return policy.tools[hold.tool_id].tool_class
        return None

    def agent_summary(agent: str) -> dict[str, Any]:
        rules = policy.agents[agent]
        now = ctx.clock.now()
        day = london_day(now).isoformat()
        flags = ctx.flags.peek(agent, now)
        if agent in ctx.passes.revoked:
            state = "revoked"
        elif agent in ctx.passes.suspended:
            state = "suspended"
        else:
            state = "active"
        return {
            "id": agent,
            "model": rules.model,
            "pass": state,
            "tools_off": sorted(ctx.tool_door.disabled.get(agent, set())),
            "tools": len(ctx.tool_door.visible_tools(agent)),
            "spend_today_usd": str(ctx.model_door.spent_today(agent)),
            "budget_usd_per_day": str(rules.budget_usd_per_day),
            "asks_today": ctx.store.ask_counts(day, agent, ctx.recorder.scope),
            "flags": flags.as_dict(),
            "holds_pending": sum(1 for h in ctx.holds.pending(now) if h.agent == agent),
            "trip_limit_gbp": str(rules.trip_limit_gbp) if rules.trip_limit_gbp else None,
        }

    def require_agent(agent: str) -> None:
        if agent not in policy.agents:
            raise HTTPException(404, "no such agent")

    @router.get("/status")
    async def status() -> dict[str, Any]:
        seq, head = ctx.store.head()
        return {
            "session_id": ctx.recorder.session_id,
            "session_label": ctx.recorder.label,
            "head": {"seq": seq, "hash": head},
            "checker": ctx.checker_name,
            "demo_anchor": ctx.anchor,
            "agents": list(policy.agents),
            "pending": len(ctx.a2a.pending),
            "holds_pending": len(ctx.holds.pending()),
            "clock": ctx.clock.describe(),
            "web_backend": ctx.web_backend,
            "telegram": "telegram" in ctx.extras,
            "scenario": ctx.scenario or None,
            "mode": ctx.mode,
            "models": {
                **{agent: rules.model for agent, rules in policy.agents.items()},
                "brief": policy.helper.model if policy.helper else None,
            },
            "brief_at": policy.brief.at if policy.brief else None,
        }

    @router.get("/agents")
    async def agents() -> list[dict[str, Any]]:
        return [agent_summary(agent) for agent in policy.agents]

    @router.get("/agents/{agent}")
    async def agent_detail(agent: str) -> dict[str, Any]:
        require_agent(agent)
        rules = policy.agents[agent]
        tools = []
        for name, tool_id in ctx.tool_door.exposures[agent].items():
            entry = ctx.catalogue.tools.get(tool_id)
            rule = policy.tools.get(tool_id)
            off = name in ctx.tool_door.disabled.get(agent, set())
            tools.append(
                {
                    "name": name,
                    "tool_id": tool_id,
                    "state": "off" if off else entry.state if entry else "missing",
                    "fingerprint": entry.fingerprint if entry else None,
                    "pinned": entry.pinned if entry else None,
                    "tool_class": rule.tool_class if rule else None,
                    "egress": rule.is_egress if rule else None,
                }
            )
        a2a_tools = [
            {"name": t.name, "tool_id": f"steward.{t.name}", "state": "built-in"}
            for t in ctx.tool_door.a2a_tools(agent)
        ]
        routes = [
            {
                "to": rule.contact,
                "request_types": list(rule.request_types),
                "detail_minutes": rule.detail_minutes,
                "daily_limit": rule.daily_limit,
                "burst_max": rule.burst_max,
                "burst_minutes": rule.burst_minutes,
            }
            for rule in policy.contacts_of(agent)
        ]
        recent = ctx.store.events(agent=agent, limit=30)
        return {
            **agent_summary(agent),
            "tool_prefix": rules.tool_prefix,
            "tool_list": tools + a2a_tools,
            "routes": routes,
            "recent": [event_json(e) for e in recent],
        }

    @router.post("/agents/{agent}/suspend")
    async def suspend(agent: str) -> dict[str, Any]:
        require_agent(agent)
        ctx.set_suspended(agent, True)
        return agent_summary(agent)

    @router.post("/agents/{agent}/resume")
    async def resume(agent: str) -> dict[str, Any]:
        require_agent(agent)
        try:
            ctx.set_suspended(agent, False)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return agent_summary(agent)

    @router.post("/agents/{agent}/revoke")
    async def revoke(agent: str) -> dict[str, Any]:
        require_agent(agent)
        ctx.revoke(agent, "owner (console)")
        return agent_summary(agent)

    @router.post("/agents/{agent}/tools/{tool}/{state}")
    async def switch_tool(agent: str, tool: str, state: str) -> dict[str, Any]:
        require_agent(agent)
        if state not in {"on", "off"}:
            raise HTTPException(404, "state must be on or off")
        try:
            ctx.set_tool(agent, tool, state == "on", "owner (console)")
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return agent_summary(agent)

    @router.get("/controls")
    async def controls() -> list[dict[str, Any]]:
        return ctx.store.controls()

    @router.get("/log")
    async def log(
        agent: str | None = None,
        kind: str | None = None,
        door: str | None = None,
        after_seq: int | None = None,
        limit: Annotated[int, Query(ge=1, le=5000)] = 100,
    ) -> list[dict[str, Any]]:
        events = ctx.store.events(
            agent=agent, kind=kind, door=door, after_seq=after_seq, limit=limit
        )
        return [event_json(e) for e in events]

    @router.get("/log/stream", response_class=EventSourceResponse)
    async def stream(after_seq: int = 0, follow: bool = True) -> AsyncIterable[ServerSentEvent]:
        if demo:
            # Nothing changes in the demo, so a visitor's stream ends after the history.
            follow = False
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)

        def listener(event: Event) -> None:
            queue.put_nowait(event)

        ctx.store.subscribe(listener)
        try:
            last = after_seq
            for event in ctx.store.events(after_seq=after_seq, limit=5000, newest_first=False):
                last = event.seq
                yield ServerSentEvent(data=event_json(event), id=str(event.seq), event="event")
            while follow:
                try:
                    event = await asyncio.wait_for(queue.get(), KEEPALIVE_S)
                except TimeoutError:
                    yield ServerSentEvent(comment="keep-alive")
                    continue
                if event.seq > last:
                    last = event.seq
                    yield ServerSentEvent(data=event_json(event), id=str(event.seq), event="event")
        finally:
            ctx.store.unsubscribe(listener)

    @router.get("/log/verify")
    async def verify() -> dict[str, Any]:
        result = ctx.store.verify()
        return {
            "ok": result.ok,
            "rows": result.rows,
            "head_hash": result.head_hash,
            "first_bad_seq": result.first_bad_seq,
            "reason": result.reason,
        }

    @router.get("/log/export.jsonl")
    async def export() -> StreamingResponse:
        lines = (line + "\n" for line in ctx.store.export_lines())
        return StreamingResponse(
            lines,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="steward-log.jsonl"'},
        )

    @router.get("/tools")
    async def tools() -> list[dict[str, Any]]:
        return [
            {
                "tool_id": entry.tool_id,
                "server": entry.server,
                "state": entry.state,
                "fingerprint": entry.fingerprint,
                "pinned": entry.pinned,
                "description": entry.tool.description,
                "tool_class": policy.tools[entry.tool_id].tool_class
                if entry.tool_id in policy.tools
                else None,
            }
            for entry in ctx.catalogue.tools.values()
        ]

    @router.post("/tools/refresh")
    async def refresh() -> list[dict[str, Any]]:
        await ctx.catalogue.refresh()
        ctx.recorder.record("admin", "admin.tools_refreshed", {"tools": len(ctx.catalogue.tools)})
        return await tools()

    @router.post("/tools/{tool_id}/repin")
    async def repin(tool_id: str) -> dict[str, Any]:
        if tool_id not in ctx.catalogue.tools:
            raise HTTPException(404, "no such tool")
        entry = ctx.catalogue.repin(tool_id)
        ctx.recorder.record(
            "admin", "admin.tool_repinned", {"tool_id": tool_id, "fingerprint": entry.fingerprint}
        )
        return {"tool_id": tool_id, "state": entry.state, "fingerprint": entry.fingerprint}

    @router.post("/session")
    async def new_session(body: SessionIn) -> dict[str, Any]:
        return {"session_id": ctx.recorder.new_session(body.label)}

    @router.get("/pending")
    async def pending() -> list[dict[str, Any]]:
        return [
            {
                "request_id": p.request_id,
                "asker": p.asker,
                "contact": p.contact,
                "request_type": p.request_type,
            }
            for p in ctx.a2a.pending.values()
        ]

    # Approvals --------------------------------------------------------------------------------

    @router.get("/holds")
    async def holds(
        state: str = "pending", limit: Annotated[int, Query(ge=1, le=500)] = 100
    ) -> list[dict[str, Any]]:
        if state == "pending":
            found = ctx.holds.pending()
        else:
            ctx.holds.pending()  # works out expiry first
            found = ctx.store.holds(state=None if state == "all" else state, limit=limit)
        # What started the agent's run, for the cards still waiting (the decided list does
        # not show it).
        return [
            hold_json(h, tool_class(h), prompted_by(ctx, h) if h.state == "pending" else None)
            for h in found[:limit]
        ]

    @router.get("/holds/{hold_id}")
    async def hold_detail(hold_id: str) -> dict[str, Any]:
        hold = ctx.holds.get(hold_id)
        if hold is None:
            raise HTTPException(404, "no such hold")
        return hold_json(hold, tool_class(hold), prompted_by(ctx, hold))

    @router.post("/holds/{hold_id}/decide")
    async def decide(hold_id: str, body: DecideIn) -> dict[str, Any]:
        try:
            hold = await ctx.decide(hold_id, body.choice, body.fingerprint, "owner (console)")
        except HoldError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        return hold_json(hold, tool_class(hold))

    @router.get("/intents")
    async def intents() -> list[dict[str, Any]]:
        return ctx.store.intents()

    @router.get("/stand-ins")
    async def stand_ins() -> list[dict[str, Any]]:
        return ctx.store.stand_ins(ctx.recorder.session_id)

    # Flags, clock and world -------------------------------------------------------------------

    @router.get("/flags")
    async def flags() -> dict[str, Any]:
        snapshot = ctx.flags.snapshot(ctx.clock.now())
        return {
            agent: snapshot.get(agent, {"private_read": [], "untrusted_seen": [], "since": None})
            for agent in policy.agents
        }

    @router.post("/flags/{agent}/clear")
    async def clear_flags(agent: str) -> dict[str, Any]:
        require_agent(agent)
        ctx.clear_flags(agent, "owner (console)")
        return await flags()

    @router.get("/clock")
    async def clock() -> dict[str, Any]:
        return ctx.clock.describe()

    @router.post("/clock")
    async def set_clock(body: ClockIn) -> dict[str, Any]:
        if body.at.tzinfo is None:
            raise HTTPException(400, "at must include a timezone")
        ctx.clock.set(body.at, body.rate)
        ctx.recorder.record("system", "system.clock", ctx.clock.describe())
        return ctx.clock.describe()

    @router.delete("/clock")
    async def reset_clock() -> dict[str, Any]:
        ctx.clock.reset()
        ctx.recorder.record("system", "system.clock", ctx.clock.describe())
        return ctx.clock.describe()

    @router.get("/world")
    async def world() -> dict[str, Any]:
        return {
            "anchor": ctx.world.anchor.isoformat(),
            "follow_clock": ctx.world.follow_clock,
            "applied": ctx.world.applied,
            "bookings": list(ctx.world.bookings.values()),
            "todo": [ctx.world.todo_item(i) for i in ctx.world.todo],
            "outbox": ctx.world.outbox,
        }

    @router.post("/world/reset")
    async def world_reset(body: WorldResetIn) -> dict[str, Any]:
        ctx.reset_world(follow_clock=body.follow_clock)
        return await world()

    @router.post("/world/change")
    async def world_change(body: WorldChangeIn) -> dict[str, Any]:
        try:
            return await ctx.apply_world_change(body.change_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    # Exchanges and policy ---------------------------------------------------------------------

    @router.get("/exchanges")
    async def exchanges(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict[str, Any]]:
        events = [
            e
            for e in ctx.store.events(door="a2a", limit=5000, newest_first=False)
            if e.kind in A2A_KINDS
        ]
        by_request: dict[str, dict[str, Any]] = {}
        items: list[dict[str, Any]] = []
        for e in events:
            p = e.payload
            if e.kind == "a2a.request":
                item = {
                    "id": p["request_id"],
                    "ts": e.ts,
                    "asker": e.agent,
                    "contact": p.get("contact"),
                    "request_type": p.get("request_type"),
                    "fields": p.get("fields"),
                    "status": "asked",
                    "asks_today": p.get("asks_today"),
                    "hold_id": p.get("hold_id"),
                }
                by_request[p["request_id"]] = item
                items.append(item)
            elif e.kind in {"a2a.refused", "a2a.held"} and "call" in p and p.get("contact"):
                items.append(
                    {
                        "id": f"seq-{e.seq}",
                        "ts": e.ts,
                        "asker": e.agent,
                        "contact": p.get("contact"),
                        "request_type": p.get("request_type"),
                        "fields": p.get("fields"),
                        "status": "held" if e.kind == "a2a.held" else "refused",
                        "rule": p.get("rule"),
                        "reason": p.get("reason"),
                        "hold_id": p.get("hold_id"),
                        "asks_today": p.get("asks_today"),
                    }
                )
            elif e.kind == "a2a.reply" and p.get("request_id") in by_request:
                by_request[p["request_id"]].update(
                    {
                        "status": "answered",
                        "reply_in": p.get("reply_in"),
                        "reply_out": p.get("reply_out"),
                        "dropped_fields": p.get("dropped_fields"),
                        "rules_fired": p.get("rules_fired"),
                        "detail_minutes": p.get("detail_minutes"),
                        "replied": e.ts,
                    }
                )
            elif e.kind == "a2a.reply_invalid" and p.get("request_id") in by_request:
                by_request[p["request_id"]].setdefault("invalid_replies", []).append(
                    {"fields": p.get("fields"), "reason": p.get("reason")}
                )
            elif e.kind in {"a2a.timeout", "a2a.no_reply"} and p.get("request_id") in by_request:
                if by_request[p["request_id"]]["status"] == "asked":
                    by_request[p["request_id"]]["status"] = "no answer"
            elif e.kind == "a2a.notice":
                items.append(
                    {
                        "id": p.get("notice_id"),
                        "ts": e.ts,
                        "asker": p.get("asker"),
                        "contact": p.get("from_side"),
                        "request_type": "change_notice",
                        "status": "notice",
                        "about": p.get("request_ids"),
                        "notices": p.get("notices"),
                    }
                )
            elif e.kind == "stand_in.issued":
                items.append(
                    {
                        "id": p.get("token"),
                        "ts": e.ts,
                        "asker": e.agent,
                        "contact": "owner",
                        "request_type": "address_for_booking",
                        "status": "stand-in issued",
                        "hold_id": p.get("hold_id"),
                        "stand_in": {k: p.get(k) for k in ("token", "trip_date", "expires")},
                    }
                )
        return list(reversed(items))[:limit]

    # Morning brief, mail and watcher (Phase 3) -------------------------------------------

    @router.get("/brief")
    async def brief() -> dict[str, Any]:
        from steward_gateway.supervisor import latest_brief

        return latest_brief(ctx) or {"state": "none"}

    @router.post("/brief/run")
    async def brief_run() -> dict[str, Any]:
        """Write a brief now (for a check; the scheduler writes it every morning)."""
        from steward_gateway.supervisor import latest_brief

        if ctx.scheduler is None or "brief" not in ctx.scheduler.jobs:
            raise HTTPException(409, "the policy has no brief section")
        await ctx.scheduler.run_job("brief", ctx.clock.now())
        return latest_brief(ctx) or {"state": "none"}

    @router.post("/brief/answer")
    async def brief_answer(body: AnswerIn) -> dict[str, Any]:
        """Answer the brief's one question: decide each of its holds with its fingerprint."""
        from steward_gateway.supervisor import latest_brief

        found = latest_brief(ctx)
        if found is None or found.get("brief_id") != body.brief_id:
            raise HTTPException(404, "no such brief, or a newer one exists")
        question = found.get("question")
        if not question:
            raise HTTPException(409, "this brief has no question")
        choice = question["approve"] if body.choice == "approve" else question["deny"]
        outcomes = []
        for item in question["holds"]:
            try:
                hold = await ctx.decide(
                    str(item["hold_id"]), choice, str(item["fingerprint"]), "owner (brief)"
                )
                outcomes.append({"hold_id": hold.hold_id, "state": hold.state})
            except HoldError as exc:
                outcomes.append({"hold_id": item["hold_id"], "error": exc.message})
        return {"brief_id": body.brief_id, "choice": choice, "outcomes": outcomes}

    @router.get("/mail")
    async def mail() -> list[dict[str, Any]]:
        """Suspected phishing Steward screened, newest first, with the owner's verdict."""
        verdicts = {c["target"]: c["data"] for c in ctx.store.controls("mail_verdict")}
        seen: dict[str, dict[str, Any]] = {}
        for e in ctx.store.events(kind="mail.screened", limit=500, newest_first=False):
            message_id = str(e.payload.get("message_id"))
            item = seen.setdefault(
                message_id,
                {**e.payload, "first_seen": e.ts, "agent": e.agent, "reads": 0},
            )
            item["reads"] += 1
        items = []
        for message_id, item in seen.items():
            item["verdict"] = (verdicts.get(message_id) or {}).get("verdict")
            item["reported_by_agent"] = message_id in ctx.world.reported
            items.append(item)
        return list(reversed(items))

    @router.post("/mail/{message_id}/verdict")
    async def mail_verdict(message_id: str, body: VerdictIn) -> dict[str, Any]:
        try:
            ctx.mail_verdict(message_id, body.verdict, "owner (console)")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"message_id": message_id, "verdict": body.verdict}

    @router.get("/watcher")
    async def watcher() -> dict[str, Any]:
        settings = policy.watcher
        pauses = [
            {"ts": e.ts, "agent": e.agent, **e.payload}
            for e in ctx.store.events(kind="watcher.paused", limit=50)
        ]
        baselines = ctx.store.events(kind="brief.baselines", limit=1)
        return {
            "on": settings is not None,
            "limits": settings.model_dump(mode="json") if settings else None,
            "agents": {
                agent: {
                    "pass": "revoked"
                    if agent in ctx.passes.revoked
                    else "paused"
                    if agent in ctx.passes.suspended
                    else "active",
                    "tools_off": sorted(ctx.tool_door.disabled.get(agent, set())),
                }
                for agent in policy.agents
            },
            "pauses": pauses,
            "baselines": baselines[0].payload.get("baselines") if baselines else [],
        }

    @router.get("/policy")
    async def policy_view() -> dict[str, Any]:
        data: dict[str, Any] = policy.model_dump(mode="json", by_alias=True)
        return data

    # Scenario and replay ----------------------------------------------------------------------

    @router.post("/scenario/run")
    async def scenario_run(body: ScenarioIn) -> dict[str, Any]:
        from steward_gateway.scenario import start_scenario

        try:
            return start_scenario(
                ctx,
                body.name,
                body.approvals,
                body.speed,
                body.attacks,
                real_time=body.real_time,
                owner_wait_min=body.owner_wait_min,
                plan=body.plan,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/scenario")
    async def scenario_state() -> dict[str, Any]:
        return ctx.scenario or {"state": "idle"}

    @router.post("/replay")
    async def replay(body: ReplayIn) -> dict[str, Any]:
        from steward_gateway.replay import replay_store

        result = await replay_store(ctx, body.session_id)
        ctx.last_replay = result
        ctx.recorder.record(
            "system",
            "replay.result",
            {k: v for k, v in result.items() if k not in {"first_difference"}}
            | {"first_difference": result.get("first_difference")},
        )
        return result

    @router.get("/replay")
    async def replay_last() -> dict[str, Any]:
        return ctx.last_replay or {"state": "none"}

    return router


def _expired(hold: Hold, now: datetime) -> bool:
    return hold.state == "pending" and parse_iso(hold.expires) <= now
