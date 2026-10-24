"""The owner's admin API, behind the owner token (Authorization: Bearer <STEWARD_OWNER_TOKEN>).

The agents' OpenShell profiles only allow /v1/** and /tools/<side>/**, so the sandboxes cannot
reach these routes even though they share the port. In Phase 1 the owner reaches them through
an SSH tunnel (D7).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from steward_core.clock import london_day, london_day_start, utc_now
from steward_core.store import Event, Store
from steward_gateway.a2a_door import AgentToAgentDoor
from steward_gateway.recorder import Recorder
from steward_gateway.settings import PassBook, Settings
from steward_gateway.tool_door import Catalogue, ToolDoor

KEEPALIVE_S = 15.0


@dataclass
class AdminContext:
    settings: Settings
    passes: PassBook
    store: Store
    recorder: Recorder
    catalogue: Catalogue
    tool_door: ToolDoor
    a2a: AgentToAgentDoor
    checker_name: str
    anchor: str


class SessionIn(BaseModel):
    label: str | None = None


def event_json(event: Event) -> dict[str, Any]:
    return event.as_dict()


def admin_router(ctx: AdminContext) -> APIRouter:
    def owner(request: Request) -> None:
        if not ctx.passes.is_owner(request.headers.get("authorization")):
            ctx.recorder.record(
                "auth", "auth.denied", {"path": request.url.path, "status": 401, "reason": "owner"}
            )
            raise HTTPException(401, "owner token required")

    router = APIRouter(prefix="/admin", dependencies=[Depends(owner)])

    def spend_today(agent: str) -> str:
        return str(ctx.store.spend_since(agent, london_day_start(london_day(utc_now()))))

    def agent_summary(agent: str) -> dict[str, Any]:
        rules = ctx.settings.policy.agents[agent]
        day = london_day(utc_now()).isoformat()
        return {
            "id": agent,
            "model": rules.model,
            "pass": "suspended" if agent in ctx.passes.suspended else "active",
            "tools": len(ctx.tool_door.visible_tools(agent)),
            "spend_today_usd": spend_today(agent),
            "budget_usd_per_day": str(rules.budget_usd_per_day),
            "asks_today": ctx.store.ask_counts(day, agent),
        }

    def require_agent(agent: str) -> None:
        if agent not in ctx.settings.policy.agents:
            raise HTTPException(404, "no such agent")

    @router.get("/status")
    async def status() -> dict[str, Any]:
        seq, head = ctx.store.head()
        return {
            "session_id": ctx.recorder.session_id,
            "head": {"seq": seq, "hash": head},
            "checker": ctx.checker_name,
            "demo_anchor": ctx.anchor,
            "agents": list(ctx.settings.policy.agents),
            "pending": len(ctx.a2a.pending),
        }

    @router.get("/agents")
    async def agents() -> list[dict[str, Any]]:
        return [agent_summary(agent) for agent in ctx.settings.policy.agents]

    @router.get("/agents/{agent}")
    async def agent_detail(agent: str) -> dict[str, Any]:
        require_agent(agent)
        rules = ctx.settings.policy.agents[agent]
        tools = []
        for name, tool_id in ctx.tool_door.exposures[agent].items():
            entry = ctx.catalogue.tools.get(tool_id)
            tools.append(
                {
                    "name": name,
                    "tool_id": tool_id,
                    "state": entry.state if entry else "missing",
                    "fingerprint": entry.fingerprint if entry else None,
                    "pinned": entry.pinned if entry else None,
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
            }
            for rule in ctx.settings.policy.contacts_of(agent)
        ]
        recent = ctx.store.events(agent=agent, limit=20)
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
        ctx.passes.suspended.add(agent)
        ctx.recorder.record("admin", "admin.pass_suspended", {"agent": agent}, agent=agent)
        return agent_summary(agent)

    @router.post("/agents/{agent}/resume")
    async def resume(agent: str) -> dict[str, Any]:
        require_agent(agent)
        ctx.passes.suspended.discard(agent)
        ctx.recorder.record("admin", "admin.pass_resumed", {"agent": agent}, agent=agent)
        return agent_summary(agent)

    @router.get("/log")
    async def log(
        agent: str | None = None,
        kind: str | None = None,
        door: str | None = None,
        after_seq: int | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict[str, Any]]:
        events = ctx.store.events(
            agent=agent, kind=kind, door=door, after_seq=after_seq, limit=limit
        )
        return [event_json(e) for e in events]

    @router.get("/log/stream", response_class=EventSourceResponse)
    async def stream(after_seq: int = 0, follow: bool = True) -> AsyncIterable[ServerSentEvent]:
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

    return router
