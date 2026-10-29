"""Scripted actors for the Manchester evening, and a recorder for the scripted evening.

ScriptedHermes does what the story says the work agent does, beat by beat, through the tool
door. chatty_personal is a careless personal agent that answers every request type with every
detail it has (titles, places, notes, reasons), the way OpenClaw sometimes does, so the trim
step has something to drop. The tests play the evening with them, and the public demo bundles
the evening they play (labelled "scripted actors through the same doors"), because it shows the
07:35 paid change that live runs have not produced.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx
from mcp import Client
from mcp.types import CallToolResult

from steward_core.policy import load_policy
from steward_core.store import Store
from steward_gateway.adapters import Adapter, MockAdapter
from steward_gateway.runtime import Runtime
from steward_gateway.settings import Settings

TRIP = {"start_date": "2026-10-12", "end_date": "2026-10-15"}
ADDRESS = {
    "booking_tool": "travel_book_taxi",
    "trip_date": "2026-10-15",
    "pickup_time": "09:00",
    "booking_summary": "Taxi to Exeter St Davids for the 09:30 train",
}


async def call(ctx: Runtime, agent: str, tool: str, args: dict[str, Any]) -> CallToolResult:
    """Call a tool on the agent's own endpoint through the in-memory MCP client."""
    async with Client(ctx.tool_door.servers[agent]) as client:
        result = await client.call_tool(tool, args)
    assert isinstance(result, CallToolResult)
    return result


async def chatty_personal(ctx: Runtime, request_id: str, message: str) -> None:
    """A careless personal agent: answers every request type with every detail it has
    (titles, places, notes, reasons), the way OpenClaw sometimes does."""
    if "availability" in message:
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
        fields: dict[str, Any] = {"blocks": blocks, "note": "Wednesday is the hospital."}
    else:
        profile = (await call(ctx, "personal", "profile_read_needs", {})).structured_content
        if "travel limits" in message:
            fields = {"limits": profile["travel_limits"]}
        else:
            fields = {
                "dietary": [{"need": d["need"], "reason": d["reason"]} for d in profile["dietary"]],
                "access": [{"need": a["need"], "reason": a["reason"]} for a in profile["access"]],
                "home": profile["home_address"],
            }
    accepted = await call(ctx, "personal", "reply", {"request_id": request_id, "fields": fields})
    assert accepted.structured_content == {"accepted": True}, accepted


class ScriptedHermes:
    """Does what the story says the work agent does, through the door, beat by beat."""

    def __init__(self) -> None:
        self.holds: list[str] = []
        self.seen: list[str] = []
        self.token: str | None = None

    async def ask(self, ctx: Runtime, request_type: str, fields: dict[str, Any]) -> Any:
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

    async def act(self, ctx: Runtime, name: str, args: dict[str, Any]) -> Any:
        result = await call(ctx, "work", name, args)
        self.seen.append(result.model_dump_json())
        data = result.structured_content or {}
        if data.get("status") == "held":
            self.holds.append(data["hold_id"])
        return result

    async def __call__(self, ctx: Runtime, key: str, message: str) -> None:
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
            listing = await self.act(ctx, "mail_list_messages", {})
            newest = listing.structured_content["messages"][-1]["id"]
            read = await self.act(ctx, "mail_read_message", {"id": newest})
            screen = (read.structured_content or {}).get("steward_screen") or {}
            if newest == "w-msg-phishing-verify":
                # A careful agent reports the phish Steward flagged and opens nothing.
                assert screen.get("suspected_phishing") is True, read
                await self.act(
                    ctx,
                    "mail_report_message",
                    {"id": newest, "reason": "asks to verify the mailbox within 24 hours"},
                )
                return
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
            told = re.search(r"\(hold (h-[0-9a-f]+)\)", message)
            assert told is not None
            await self.act(ctx, "hold_status", {"hold_id": told.group(1)})


async def record_scripted(policy_path: Path | str = "config/steward.yaml") -> list[dict[str, Any]]:
    """Play the Manchester evening with the scripted actors in an in-memory Steward (no key, no
    sandbox, no model: the brief comes from the template) and return the session's events."""
    from steward_gateway.app import create_app
    from steward_gateway.replay import pick_session
    from steward_gateway.scenario import run_manchester

    policy = load_policy(policy_path)
    settings = Settings(
        nebius_api_key="scripted-no-key",
        owner_token="scripted-owner",
        passes={agent: f"scripted-pass-{agent}" for agent in policy.agents},
        policy=policy,
        db_path=Path(":memory:"),
        console_dir=Path("scripted-no-console"),
        web_backend="fixture",
    )
    holder: dict[str, Runtime] = {}
    hermes = ScriptedHermes()

    async def personal(request_id: str, message: str) -> None:
        await chatty_personal(holder["rt"], request_id, message)

    async def work(request_id: str, message: str) -> None:
        await hermes(holder["rt"], request_id, message)

    adapters: dict[str, Adapter] = {"personal": MockAdapter(personal), "work": MockAdapter(work)}
    app = create_app(
        settings,
        # No model behind the scripted evening: the brief writer is told so and the template
        # writes the brief.
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        adapters=adapters,
        store=Store(":memory:"),
        # The actors' dates are the story's own (Sun 11 Oct to Thu 15 Oct 2026).
        anchor_value="replay",
    )
    runtime: Runtime = app.state.steward
    holder["rt"] = runtime
    async with app.router.lifespan_context(app):
        await run_manchester(runtime, "scripted", 0, "agent")
        for _ in range(20):
            await asyncio.sleep(0.01)
        lines = [json.loads(line) for line in runtime.store.export_lines()]
    return pick_session(lines, None)
