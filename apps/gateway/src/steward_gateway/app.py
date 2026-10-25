"""Steward: the model door, the tool door (with the agent-to-agent door inside it), the admin API
and the console, in one FastAPI process.

Run on the VM with one worker (open agent-to-agent requests live in memory). Sandboxes reach it
as host.openshell.internal:8000; the security group keeps 8000 closed to the internet:

    uv run --package steward-gateway --env-file .env \
        uvicorn steward_gateway.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mcp.server import MCPServer, Server
from mcp.server.transport_security import TransportSecuritySettings

from steward_core.checker import make_checker
from steward_core.clock import StewardClock
from steward_core.policy import exposed_name
from steward_core.rules import FlagBook
from steward_core.store import Hold, Store
from steward_fixtures import World, home_address_parts, resolve_anchor
from steward_fixtures.servers import build_servers, demo_window
from steward_gateway.a2a_door import AgentToAgentDoor
from steward_gateway.adapters import Adapter, HermesRunsAdapter, OpenClawExecAdapter
from steward_gateway.admin import admin_router
from steward_gateway.holds import FINAL, HoldBook
from steward_gateway.links import links_router
from steward_gateway.model_door import UPSTREAM_TIMEOUT, ModelDoor
from steward_gateway.recorder import Recorder
from steward_gateway.runtime import Runtime
from steward_gateway.settings import PassBook, Settings
from steward_gateway.telegram_cards import TelegramCards
from steward_gateway.tool_door import Catalogue, Gate, PassGate, ToolDoor
from steward_gateway.web_tools import make_backend, web_server

VERSION = "0.2.0"
# The MCP apps answer 421 to any other Host header. Sandboxes call host.openshell.internal:8000.
ALLOWED_HOSTS = [
    "host.openshell.internal",
    "host.openshell.internal:*",
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
]
ALLOWED_ORIGINS = ["http://host.openshell.internal:*", "http://127.0.0.1:*", "http://localhost:*"]
# The SDK drops an MCP session after 30 idle minutes. Hermes keeps one session for its whole
# life, and its first call after the drop fails ("outcome UNKNOWN") before it reconnects, so idle
# sessions are kept for a day instead. OpenClaw opens a fresh session for every run.
MCP_SESSION_IDLE_S = 24 * 60 * 60
# The agent that may see the vault (the home address): the personal side holds the profile.
VAULT_READERS = frozenset({"personal"})


def default_adapters(settings: Settings) -> dict[str, Adapter]:
    adapters: dict[str, Adapter] = {}
    for name, agent in settings.policy.agents.items():
        delivery = agent.delivery
        if delivery and delivery.kind == "openclaw-exec" and delivery.sandbox:
            adapters[name] = OpenClawExecAdapter(
                delivery.sandbox, timeout_s=delivery.timeout_s, command=[settings.openshell_bin]
            )
        elif delivery and delivery.kind == "hermes-runs" and delivery.url:
            key = settings.delivery_keys.get(name)
            if key:
                adapters[name] = HermesRunsAdapter(delivery.url, key, timeout_s=delivery.timeout_s)
    return adapters


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    upstream_servers: Mapping[str, Server[Any] | MCPServer] | None = None,
    adapters: Mapping[str, Adapter] | None = None,
    store: Store | None = None,
    clock: StewardClock | None = None,
    world: World | None = None,
    gate: Gate | None = None,
    session_id: str | None = None,
    anchor_value: str | None = None,
) -> FastAPI:
    """Build Steward. The keyword arguments replace the real parts in tests and replay."""
    cfg = settings or Settings.from_env()
    policy = cfg.policy
    steward_clock = clock or StewardClock()
    db = store or Store(cfg.db_path)
    recorder = Recorder(db, session_id, clock=steward_clock)
    passes = PassBook(cfg.passes, cfg.owner_token)
    anchor = resolve_anchor(anchor_value)
    the_world = world or World(anchor, clock=steward_clock.now)
    checker = make_checker(policy.checker, policy.checker_timeout_s)
    holds = HoldBook(db, recorder, policy, steward_clock)
    flags = FlagBook()
    a2a = AgentToAgentDoor(
        policy,
        db,
        recorder,
        checker,
        default_adapters(cfg) if adapters is None else adapters,
        holds,
        steward_clock,
        window=demo_window(anchor),
    )
    backend = make_backend(cfg.tavily_api_key, cfg.web_backend, cfg.demo_page_url)
    if upstream_servers is None:
        servers: dict[str, Server[Any] | MCPServer] = dict(build_servers(the_world))
        servers["web"] = web_server(backend)
    else:
        servers = dict(upstream_servers)
    catalogue = Catalogue(servers, db, recorder)
    tool_door = ToolDoor(
        policy,
        passes,
        recorder,
        catalogue,
        a2a,
        holds,
        flags,
        steward_clock,
        vault={"address": home_address_parts()},
        vault_readers=VAULT_READERS,
        gate=gate,
    )
    model_door = ModelDoor(cfg, passes, recorder, a2a.active_request, steward_clock)

    def pin_world(moment: datetime | None) -> None:
        the_world.pinned = moment

    tool_door.pin_time = pin_world

    async def read_calendar(agent: str, start: str, end: str) -> list[dict[str, Any]] | None:
        tool_id = next(
            (t for t in policy.agents[agent].tools if exposed_name(t) == "calendar_list_events"),
            None,
        )
        if tool_id is None:
            return None
        found = await tool_door._internal(tool_id, {"start_date": start, "end_date": end})
        events = (found or {}).get("events")
        return events if isinstance(events, list) else None

    a2a.read_calendar = read_calendar

    def tell_agent(hold: Hold, event: str) -> None:
        # P2-D9 B: tell the agent when a hold of a notify class is decided.
        if event != "decided" or hold.kind != "tool" or hold.state not in FINAL:
            return
        if hold.tool_id is None:
            return
        if policy.tool_rule(hold.tool_id).tool_class not in policy.holds.notify_classes:
            return
        outcome = {
            "done": "was approved, and Steward made the change once",
            "denied": "was declined by the owner; nothing was changed",
            "failed": "was approved, but the change failed; the owner has the details",
            "unknown": "was approved, but its outcome is unknown; the owner has the details",
            "expired": "expired; nothing was changed",
        }.get(hold.state, hold.state)
        text = (
            f"Steward: your held call {hold.tool} (hold {hold.hold_id}) {outcome}. "
            f"Call hold_status with hold_id {hold.hold_id} for the result, then update the plan."
        )
        task = asyncio.get_running_loop().create_task(
            a2a.tell(hold.agent, f"t-{hold.hold_id}", text)
        )
        pending_tells.add(task)
        task.add_done_callback(pending_tells.discard)

    pending_tells: set[asyncio.Task[None]] = set()
    holds.listeners.append(tell_agent)

    runtime = Runtime(
        settings=cfg,
        passes=passes,
        store=db,
        recorder=recorder,
        clock=steward_clock,
        world=the_world,
        catalogue=catalogue,
        tool_door=tool_door,
        a2a=a2a,
        holds=holds,
        flags=flags,
        model_door=model_door,
        checker_name=checker.name,
        anchor=anchor.isoformat(),
        web_backend=backend.name if upstream_servers is None else "custom",
    )
    telegram = TelegramCards.from_settings(cfg, runtime)
    if telegram is not None:
        holds.listeners.append(telegram.on_hold)
        runtime.extras["telegram"] = telegram

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=ALLOWED_HOSTS,
        allowed_origins=ALLOWED_ORIGINS,
    )
    # streamable_http_app() must run before session_manager exists.
    tool_apps = {
        agent: server.streamable_http_app(
            streamable_http_path="/mcp",
            transport_security=security,
            session_idle_timeout=MCP_SESSION_IDLE_S,
        )
        for agent, server in tool_door.servers.items()
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # A mounted sub-app's lifespan never runs, so each MCP session manager is entered here.
        async with AsyncExitStack() as stack:
            recorder.record(
                "system",
                "system.start",
                {
                    "version": VERSION,
                    "checker": checker.name,
                    "agents": sorted(policy.agents),
                    "demo_anchor": anchor.isoformat(),
                    "web_backend": runtime.web_backend,
                    "telegram": telegram is not None,
                },
            )
            await catalogue.start(stack)
            for server in tool_door.servers.values():
                await stack.enter_async_context(server.session_manager.run())
            model_door.client = await stack.enter_async_context(
                httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT, transport=transport)
            )
            if telegram is not None:
                await telegram.start()
                stack.push_async_callback(telegram.stop)
            try:
                yield
            finally:
                recorder.record("system", "system.stop", {"pending": len(a2a.pending)})

    app = FastAPI(
        title="Steward",
        version=VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(model_door.router())
    app.include_router(admin_router(runtime))
    app.include_router(links_router(runtime))
    for agent, tool_app in tool_apps.items():
        app.mount(f"/tools/{agent}", PassGate(tool_app, agent, passes, recorder))
    if cfg.console_dir.is_dir():
        app.mount("/console", StaticFiles(directory=cfg.console_dir, html=True), name="console")
    # Tests and tools reach the running parts through here.
    app.state.steward = runtime
    return app


def __getattr__(name: str) -> Any:
    # `uvicorn steward_gateway.app:app` builds the app on first access, so importing the module
    # (tests, tooling) does not need the settings.
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
