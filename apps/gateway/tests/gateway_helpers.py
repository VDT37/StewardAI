"""Shared test helpers for the gateway (on sys.path through pytest's pythonpath setting)."""

import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.types import CallToolResult

from steward_core.clock import StewardClock
from steward_core.policy import Policy, load_policy
from steward_core.store import Store
from steward_fixtures import at, resolve_anchor
from steward_gateway.adapters import Adapter, MockAdapter
from steward_gateway.admin import AdminContext
from steward_gateway.app import create_app
from steward_gateway.settings import Settings

ROOT = Path(__file__).resolve().parents[3]
KEY = "tf-real-key-not-for-agents"
WORK = "stw_work_pass_for_tests"
PERSONAL = "stw_personal_pass_for_tests"
OWNER = "stw_owner_token_for_tests"
BASE = "http://127.0.0.1:8000"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_settings(tmp_path: Path, policy: Policy | None = None) -> Settings:
    env = {
        "NEBIUS_API_KEY": KEY,
        "STEWARD_OWNER_TOKEN": OWNER,
        "STEWARD_PASS_WORK": WORK,
        "STEWARD_PASS_PERSONAL": PERSONAL,
        "STEWARD_POLICY": str(ROOT / "config" / "steward.yaml"),
    }
    settings = replace(
        Settings.from_env(env), db_path=tmp_path / "steward.db", console_dir=tmp_path / "none"
    )
    return replace(settings, policy=policy) if policy is not None else settings


def fast_policy(**a2a: float) -> Policy:
    base = load_policy(ROOT / "config" / "steward.yaml")
    return base.model_copy(update={"a2a": base.a2a.model_copy(update=a2a)})


def without_burst(policy: Policy) -> Policy:
    contacts = tuple(rule.model_copy(update={"burst_max": None}) for rule in policy.contacts)
    return policy.model_copy(update={"contacts": contacts})


class Upstream:
    """Fake Token Factory that records what Steward sent."""

    def __init__(self, stream_body: bytes = b"") -> None:
        self.requests: list[httpx.Request] = []
        self.stream_body = stream_body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import json

        self.requests.append(request)
        body = json.loads(request.content)
        if body.get("stream"):
            return httpx.Response(
                200,
                stream=Chunks(self.stream_body),
                headers={
                    "content-type": "text/event-stream",
                    "x-ratelimit-remaining-requests": "59",
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{"function": {"name": "calendar_list_events"}}],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 13000, "completion_tokens": 500, "total_tokens": 13500},
            },
            headers={"x-ratelimit-remaining-tokens": "399000"},
        )


class Chunks(httpx.AsyncByteStream):
    """A response body that arrives in small pieces, like a real SSE stream."""

    def __init__(self, body: bytes, size: int = 11) -> None:
        self.body = body
        self.size = size

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for i in range(0, len(self.body), self.size):
            yield self.body[i : i + self.size]


@contextmanager
def http_app(
    tmp_path: Path, upstream: Upstream | None = None, policy: Policy | None = None
) -> Iterator[tuple[TestClient, Upstream, AdminContext]]:
    fake = upstream or Upstream()
    app = create_app(
        make_settings(tmp_path, policy), transport=httpx.MockTransport(fake), adapters={}
    )
    with TestClient(app, base_url=BASE) as client:
        yield client, fake, app.state.steward


def kinds(ctx: AdminContext) -> list[str]:
    return [e.kind for e in ctx.store.events(limit=1000, newest_first=False)]


Responder = Callable[[AdminContext, str, str], Awaitable[None]]


@asynccontextmanager
async def running(
    tmp_path: Path,
    responder: Responder | None = None,
    *,
    policy: Policy | None = None,
    store: Store | None = None,
    upstream_servers: Mapping[str, Any] | None = None,
    work: Responder | None = None,
    clock: StewardClock | None = None,
) -> AsyncIterator[tuple[FastAPI, AdminContext, MockAdapter]]:
    """Steward with its lifespan running in this event loop, a scripted personal agent and,
    with work=, a scripted work agent (for notices, told holds and scenario beats)."""
    holder: dict[str, AdminContext] = {}

    async def respond(request_id: str, message: str) -> None:
        if responder is not None:
            await responder(holder["ctx"], request_id, message)

    async def respond_work(request_id: str, message: str) -> None:
        if work is not None:
            await work(holder["ctx"], request_id, message)

    adapter = MockAdapter(respond)
    adapters: dict[str, Adapter] = {"personal": adapter, "work": MockAdapter(respond_work)}
    app = create_app(
        make_settings(tmp_path, policy),
        transport=httpx.MockTransport(Upstream()),
        adapters=adapters,
        store=store,
        upstream_servers=upstream_servers,
        clock=clock,
    )
    ctx: AdminContext = app.state.steward
    holder["ctx"] = ctx
    async with app.router.lifespan_context(app):
        yield app, ctx, adapter


async def call(ctx: AdminContext, agent: str, tool: str, args: dict[str, Any]) -> CallToolResult:
    """Call a tool on the agent's own endpoint through the in-memory MCP client."""
    async with Client(ctx.tool_door.servers[agent]) as client:
        result = await client.call_tool(tool, args)
    assert isinstance(result, CallToolResult)
    return result


async def chatty_personal(ctx: AdminContext, request_id: str, message: str) -> None:
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


def story_clock(day: int = -1, hhmm: str = "19:00") -> StewardClock:
    """A Steward clock frozen at a story time (replay anchor)."""
    clock = StewardClock()
    clock.set(at(resolve_anchor("replay"), day, hhmm), rate=0)
    return clock


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
            hold_id = re.search(r"\(hold (h-[0-9a-f]+)\)", message)
            assert hold_id is not None
            await self.act(ctx, "hold_status", {"hold_id": hold_id.group(1)})
