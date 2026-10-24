"""Steward: the model door, the tool door (with the agent-to-agent door inside it), the admin API
and the console, in one FastAPI process.

Run on the VM with one worker (open agent-to-agent requests live in memory). Sandboxes reach it
as host.openshell.internal:8000; the security group keeps 8000 closed to the internet:

    uv run --package steward-gateway --env-file .env \
        uvicorn steward_gateway.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mcp.server import MCPServer, Server
from mcp.server.transport_security import TransportSecuritySettings

from steward_core.checker import make_checker
from steward_core.store import Store
from steward_fixtures import resolve_anchor
from steward_fixtures.servers import build_servers, demo_window
from steward_gateway.a2a_door import AgentToAgentDoor
from steward_gateway.adapters import Adapter, OpenClawExecAdapter
from steward_gateway.admin import AdminContext, admin_router
from steward_gateway.model_door import UPSTREAM_TIMEOUT, ModelDoor
from steward_gateway.recorder import Recorder
from steward_gateway.settings import PassBook, Settings
from steward_gateway.tool_door import Catalogue, PassGate, ToolDoor

VERSION = "0.1.0"
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


def default_adapters(settings: Settings) -> dict[str, Adapter]:
    adapters: dict[str, Adapter] = {}
    for name, agent in settings.policy.agents.items():
        delivery = agent.delivery
        if delivery and delivery.kind == "openclaw-exec" and delivery.sandbox:
            adapters[name] = OpenClawExecAdapter(
                delivery.sandbox, timeout_s=delivery.timeout_s, command=[settings.openshell_bin]
            )
    return adapters


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    upstream_servers: Mapping[str, Server[Any] | MCPServer] | None = None,
    adapters: Mapping[str, Adapter] | None = None,
    store: Store | None = None,
) -> FastAPI:
    """Build Steward. transport, upstream_servers, adapters and store replace the real ones in
    tests."""
    cfg = settings or Settings.from_env()
    db = store or Store(cfg.db_path)
    recorder = Recorder(db)
    passes = PassBook(cfg.passes, cfg.owner_token)
    anchor = resolve_anchor()
    checker = make_checker(cfg.policy.checker, cfg.policy.checker_timeout_s)
    a2a = AgentToAgentDoor(
        cfg.policy,
        db,
        recorder,
        checker,
        default_adapters(cfg) if adapters is None else adapters,
        window=demo_window(anchor),
    )
    catalogue = Catalogue(
        build_servers(anchor) if upstream_servers is None else upstream_servers, db, recorder
    )
    tool_door = ToolDoor(cfg.policy, passes, recorder, catalogue, a2a)
    model_door = ModelDoor(cfg, passes, recorder, a2a.active_request)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=ALLOWED_HOSTS,
        allowed_origins=ALLOWED_ORIGINS,
    )
    # streamable_http_app() must run before session_manager exists.
    tool_apps = {
        agent: server.streamable_http_app(streamable_http_path="/mcp", transport_security=security)
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
                    "agents": sorted(cfg.policy.agents),
                    "demo_anchor": anchor.isoformat(),
                },
            )
            await catalogue.start(stack)
            for server in tool_door.servers.values():
                await stack.enter_async_context(server.session_manager.run())
            model_door.client = await stack.enter_async_context(
                httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT, transport=transport)
            )
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

    admin = AdminContext(
        cfg, passes, db, recorder, catalogue, tool_door, a2a, checker.name, anchor.isoformat()
    )
    app.include_router(model_door.router())
    app.include_router(admin_router(admin))
    for agent, tool_app in tool_apps.items():
        app.mount(f"/tools/{agent}", PassGate(tool_app, agent, passes, recorder))
    if cfg.console_dir.is_dir():
        app.mount("/console", StaticFiles(directory=cfg.console_dir, html=True), name="console")
    # Tests and tools reach the running parts through here.
    app.state.steward = admin
    return app


def __getattr__(name: str) -> Any:
    # `uvicorn steward_gateway.app:app` builds the app on first access, so importing the module
    # (tests, tooling) does not need the settings.
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
