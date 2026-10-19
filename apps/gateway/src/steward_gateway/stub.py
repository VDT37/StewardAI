"""Phase 0 stub Steward: the smallest gateway both agents can point at.

Model door: OpenAI-compatible chat completions forwarded to Nebius Token Factory with the real
key, with the model forced to the pass's model. Tool door: one read-only MCP tool per side.
Every call is one JSON line in var/stub-log.jsonl (not hash-chained yet).

Run on the VM (sandboxes reach it as host.openshell.internal:8000; the security group keeps 8000
closed to the internet):

    uv run --package steward-gateway --env-file .env \
        uvicorn steward_gateway.stub:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from steward_fixtures import CalendarEvent, events_between, resolve_anchor

Agent = Literal["work", "personal"]
AGENTS: tuple[Agent, ...] = ("work", "personal")

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1"
REQUIRED_ENV = (
    "NEBIUS_API_KEY",
    "STEWARD_PASS_WORK",
    "STEWARD_PASS_PERSONAL",
    "STEWARD_MODEL_WORK",
    "STEWARD_MODEL_PERSONAL",
)
# httpx's 5 s default would cut off long generations.
UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
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
# Upstream response headers worth passing back to the agent.
RELAYED_HEADER_PREFIXES = ("x-ratelimit-",)
MAX_SCAN_LINE = 1_000_000


@dataclass(frozen=True)
class Settings:
    nebius_api_key: str
    passes: Mapping[Agent, str]
    models: Mapping[Agent, str]
    upstream_url: str = TOKEN_FACTORY_URL
    log_path: Path = field(default_factory=lambda: Path("var/stub-log.jsonl"))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Read the five settings, failing on any missing or empty value or equal passes."""
        source = os.environ if env is None else env
        values = {name: source.get(name, "").strip() for name in REQUIRED_ENV}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError("missing or empty settings: " + ", ".join(missing))
        if values["STEWARD_PASS_WORK"] == values["STEWARD_PASS_PERSONAL"]:
            raise RuntimeError("STEWARD_PASS_WORK and STEWARD_PASS_PERSONAL must differ")
        return cls(
            nebius_api_key=values["NEBIUS_API_KEY"],
            passes={
                "work": values["STEWARD_PASS_WORK"],
                "personal": values["STEWARD_PASS_PERSONAL"],
            },
            models={
                "work": values["STEWARD_MODEL_WORK"],
                "personal": values["STEWARD_MODEL_PERSONAL"],
            },
        )

    def agent_for(self, authorization: str | None) -> Agent | None:
        """Map an Authorization header to the agent whose pass it carries."""
        if not authorization:
            return None
        scheme, _, token = authorization.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            return None
        found: Agent | None = None
        for agent in AGENTS:
            if secrets.compare_digest(token.encode(), self.passes[agent].encode()):
                found = agent
        return found


class StubLog:
    """Append-only JSON lines. Never given a pass, a key or a request body."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        *,
        agent: Agent | None,
        door: Literal["model", "tool", "auth"],
        name: str,
        status: int,
        started: float,
        path: str | None = None,
        stream: bool = False,
        usage: Any = None,
        tool_calls: list[str] | None = None,
    ) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "agent": agent,
            "door": door,
            "name": name,
            "path": path,
            "stream": stream,
            "status": status,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "usage": usage,
            "tool_calls": tool_calls or [],
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def error_body(message: str, kind: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": kind}}


def tool_call_names(message_or_delta: Any) -> list[str]:
    names: list[str] = []
    if isinstance(message_or_delta, dict):
        for call in message_or_delta.get("tool_calls") or []:
            name = (call.get("function") or {}).get("name") if isinstance(call, dict) else None
            if name:
                names.append(str(name))
    return names


def summarise_completion(content: bytes) -> tuple[Any, list[str]]:
    """Usage and tool-call names from a non-streaming body; best effort."""
    try:
        payload = json.loads(content)
    except ValueError:
        return None, []
    if not isinstance(payload, dict):
        return None, []
    names: list[str] = []
    for choice in payload.get("choices") or []:
        if isinstance(choice, dict):
            names += tool_call_names(choice.get("message"))
    return payload.get("usage"), names


class StreamScanner:
    """Watches relayed SSE bytes for the usage chunk and tool-call names.

    It only reads copies of the bytes; any parsing failure is swallowed so the relay never
    breaks.
    """

    def __init__(self) -> None:
        self.usage: Any = None
        self.tool_calls: list[str] = []
        self._buffer = b""

    def feed(self, chunk: bytes) -> None:
        try:
            self._buffer += chunk
            *lines, self._buffer = self._buffer.split(b"\n")
            if len(self._buffer) > MAX_SCAN_LINE:
                self._buffer = b""
            for line in lines:
                self._line(line.strip())
        except Exception:
            self._buffer = b""

    def _line(self, line: bytes) -> None:
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if not data.startswith(b"{"):
            return
        try:
            event = json.loads(data)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        if event.get("usage"):
            self.usage = event["usage"]
        for choice in event.get("choices") or []:
            if isinstance(choice, dict):
                for name in tool_call_names(choice.get("delta")):
                    if name not in self.tool_calls:
                        self.tool_calls.append(name)


class PassGate:
    """ASGI middleware in front of one side's MCP app: 401 unknown pass, 403 wrong side.

    The SDK's own custom routes are never authenticated, so auth lives here.
    """

    def __init__(self, app: ASGIApp, side: Agent, settings: Settings, log: StubLog) -> None:
        self.app = app
        self.side = side
        self.settings = settings
        self.log = log

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        agent = self.settings.agent_for(headers.get("authorization"))
        path = f"/tools/{self.side}/mcp"
        if agent is None:
            self.log.write(
                agent=None, door="auth", name="mcp", path=path, status=401, started=started
            )
            response = JSONResponse(
                error_body("missing or unknown pass", "authentication_error"), 401
            )
            await response(scope, receive, send)
            return
        if agent != self.side:
            self.log.write(
                agent=agent, door="auth", name="mcp", path=path, status=403, started=started
            )
            response = JSONResponse(
                error_body(f"pass is not valid for {path}", "permission_error"), 403
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def event_json(event: CalendarEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "location": event.location,
        "notes": event.notes,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "can_move": event.can_move,
    }


def build_tool_server(side: Agent, log: StubLog) -> MCPServer:
    server: MCPServer = MCPServer(
        name=f"steward-{side}",
        instructions=f"Steward tools for the {side} agent (Phase 0 stub, read-only).",
    )

    @server.tool(
        description=(
            f"List the {side} calendar events between two dates, inclusive. "
            "Dates are YYYY-MM-DD; times are Europe/London."
        )
    )
    def list_calendar_events(start_date: str, end_date: str) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
            if end < start:
                raise ValueError("end_date is before start_date")
            events = events_between(side, start, end, resolve_anchor())
        except ValueError:
            log.write(
                agent=side, door="tool", name="list_calendar_events", status=400, started=started
            )
            raise
        log.write(agent=side, door="tool", name="list_calendar_events", status=200, started=started)
        return {"side": side, "events": [event_json(e) for e in events]}

    return server


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    """Build the stub. transport replaces the upstream connection in tests."""
    cfg = settings or Settings.from_env()
    log = StubLog(cfg.log_path)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=ALLOWED_HOSTS,
        allowed_origins=ALLOWED_ORIGINS,
    )
    tool_servers = {side: build_tool_server(side, log) for side in AGENTS}
    # streamable_http_app() must run before session_manager exists.
    tool_apps = {
        side: server.streamable_http_app(streamable_http_path="/mcp", transport_security=security)
        for side, server in tool_servers.items()
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # A mounted sub-app's lifespan never runs, so each MCP session manager is entered here.
        async with AsyncExitStack() as stack:
            for server in tool_servers.values():
                await stack.enter_async_context(server.session_manager.run())
            app.state.upstream = await stack.enter_async_context(
                httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT, transport=transport)
            )
            yield

    app = FastAPI(
        title="Steward stub", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def list_models(request: Request) -> Response:
        started = time.perf_counter()
        agent = cfg.agent_for(request.headers.get("authorization"))
        if agent is None:
            log.write(
                agent=None,
                door="auth",
                name="models",
                path="/v1/models",
                status=401,
                started=started,
            )
            return JSONResponse(error_body("missing or unknown pass", "authentication_error"), 401)
        model = cfg.models[agent]
        log.write(
            agent=agent, door="model", name=model, path="/v1/models", status=200, started=started
        )
        return JSONResponse(
            {
                "object": "list",
                "data": [{"id": model, "object": "model", "created": 0, "owned_by": "steward"}],
            }
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        started = time.perf_counter()
        path = "/v1/chat/completions"
        agent = cfg.agent_for(request.headers.get("authorization"))
        if agent is None:
            log.write(agent=None, door="auth", name="chat", path=path, status=401, started=started)
            return JSONResponse(error_body("missing or unknown pass", "authentication_error"), 401)
        model = cfg.models[agent]
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            log.write(agent=agent, door="model", name=model, path=path, status=400, started=started)
            return JSONResponse(
                error_body("body must be a JSON object", "invalid_request_error"), 400
            )

        # Phase 0 stand-in for per-pass routing: the pass decides the model, not the agent.
        body["model"] = model
        stream = body.get("stream") is True
        headers = {
            "Authorization": f"Bearer {cfg.nebius_api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            # The agent's Accept-Encoding is not forwarded; identity keeps raw bytes relayable.
            "Accept-Encoding": "identity",
        }
        client: httpx.AsyncClient = request.app.state.upstream
        upstream_request = client.build_request(
            "POST", f"{cfg.upstream_url}/chat/completions", json=body, headers=headers
        )
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            log.write(
                agent=agent,
                door="model",
                name=model,
                path=path,
                stream=stream,
                status=502,
                started=started,
            )
            message = f"upstream unreachable: {type(exc).__name__}"
            return JSONResponse(error_body(message, "upstream_error"), 502)

        relayed = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower().startswith(RELAYED_HEADER_PREFIXES)
        }
        media_type = upstream.headers.get("content-type")

        if not stream:
            try:
                content = await upstream.aread()
            finally:
                await upstream.aclose()
            usage, calls = summarise_completion(content)
            log.write(
                agent=agent,
                door="model",
                name=model,
                path=path,
                status=upstream.status_code,
                started=started,
                usage=usage,
                tool_calls=calls,
            )
            return Response(
                content, upstream.status_code, relayed, media_type or "application/json"
            )

        if "content-encoding" in upstream.headers:
            relayed["content-encoding"] = upstream.headers["content-encoding"]
        scanner = StreamScanner()

        async def relay() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    scanner.feed(chunk)
                    yield chunk
            finally:
                await upstream.aclose()
                log.write(
                    agent=agent,
                    door="model",
                    name=model,
                    path=path,
                    stream=True,
                    status=upstream.status_code,
                    started=started,
                    usage=scanner.usage,
                    tool_calls=scanner.tool_calls,
                )

        return StreamingResponse(
            relay(), upstream.status_code, relayed, media_type or "text/event-stream"
        )

    for side, tool_app in tool_apps.items():
        app.mount(f"/tools/{side}", PassGate(tool_app, side, cfg, log))
    return app


def __getattr__(name: str) -> Any:
    # `uvicorn steward_gateway.stub:app` builds the app on first access, so importing the
    # module (tests, tooling) does not need the five settings.
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
