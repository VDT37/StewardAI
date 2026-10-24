"""Shared test helpers for the gateway (on sys.path through pytest's pythonpath setting)."""

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

from steward_core.policy import Policy, load_policy
from steward_core.store import Store
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
) -> AsyncIterator[tuple[FastAPI, AdminContext, MockAdapter]]:
    """Steward with its lifespan running in this event loop, and a scripted personal agent."""
    holder: dict[str, AdminContext] = {}

    async def respond(request_id: str, message: str) -> None:
        if responder is not None:
            await responder(holder["ctx"], request_id, message)

    adapter = MockAdapter(respond)
    adapters: dict[str, Adapter] = {"personal": adapter}
    app = create_app(
        make_settings(tmp_path, policy),
        transport=httpx.MockTransport(Upstream()),
        adapters=adapters,
        store=store,
        upstream_servers=upstream_servers,
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
