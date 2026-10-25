"""The model door: an OpenAI-compatible proxy to Nebius Token Factory.

The pass decides the agent and the agent decides the model. Steward forwards with the real key,
masks listed strings first, forces usage on streamed calls so every call is metered, relays the
answer byte for byte and writes a model.call event with tokens and cost. Message contents are
not logged (only usage, tool-call names and which mask entries fired).

Phase 2: budgets block. Before forwarding, the door adds up the agent's metered spend for the
current Europe/London day (on Steward's clock); at or over the budget the call is refused with
HTTP 429 and an OpenAI-style error of type budget_exceeded, and model.budget_refused is logged.
The call that crosses the budget still completes, because its cost is only known afterwards.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from steward_core.clock import Clock, london_day, london_day_end, london_day_start
from steward_core.metering import call_cost, mask_body
from steward_gateway.recorder import Recorder
from steward_gateway.settings import PassBook, Settings

# httpx's 5 s default would cut off long generations.
UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
# Upstream response headers worth passing back to the agent.
RELAYED_HEADER_PREFIXES = ("x-ratelimit-",)
MAX_SCAN_LINE = 1_000_000


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


def usage_tokens(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    return {k: usage[k] for k in keys if isinstance(usage.get(k), int)}


class ModelDoor:
    def __init__(
        self,
        settings: Settings,
        passes: PassBook,
        recorder: Recorder,
        active_request: Callable[[str], str | None],
        clock: Clock,
    ) -> None:
        self.settings = settings
        self.passes = passes
        self.recorder = recorder
        # The open agent-to-agent request this agent is answering, if any (for the log only).
        self.active_request = active_request
        self.clock = clock
        self.client: httpx.AsyncClient | None = None

    def spent_today(self, agent: str) -> Decimal:
        """The agent's metered spend for the current London day. A scenario run counts only
        its own session, so repeated runs of the story day start from zero."""
        day = london_day(self.clock.now())
        session = None if self.recorder.scope == "live" else self.recorder.session_id
        return self.recorder.store.spend_since(
            agent, london_day_start(day), until_ts=london_day_end(day), session_id=session
        )

    def over_budget(self, agent: str, model: str) -> Response | None:
        """A 429 response (and a logged refusal) when the agent has used its daily budget."""
        budget = self.settings.policy.agents[agent].budget_usd_per_day
        spent = self.spent_today(agent)
        if spent < budget:
            return None
        self.recorder.record(
            "model",
            "model.budget_refused",
            {"model": model, "spent_usd": str(spent), "budget_usd": str(budget)},
            agent=agent,
        )
        message = f"daily budget reached: spent ${spent} of ${budget}; ask the owner"
        return JSONResponse(error_body(message, "budget_exceeded"), 429)

    def replay_call(self, agent: str, recorded: dict[str, Any]) -> None:
        """Replay: run the budget check for a recorded call and meter it again, no upstream."""
        model = str(recorded.get("model") or self.settings.policy.agents[agent].model)
        if self.over_budget(agent, model) is not None:
            return
        usage = recorded.get("usage")
        self._record(
            agent,
            model,
            0.0,
            status=int(recorded.get("status") or 200),
            stream=bool(recorded.get("stream")),
            usage=usage,
            tool_calls=list(recorded.get("tool_calls") or []),
            mask_fired=list(recorded.get("mask_fired") or []),
            a2a_request=recorded.get("a2a_request"),
            error=recorded.get("error"),
        )

    def _denied(self, request: Request, path: str) -> tuple[str | None, Response | None]:
        agent = self.passes.identify(request.headers.get("authorization"))
        if agent is None:
            self.recorder.record("auth", "auth.denied", {"path": path, "status": 401})
            return None, JSONResponse(
                error_body("missing or unknown pass", "authentication_error"), 401
            )
        if agent in self.passes.suspended:
            self.recorder.record(
                "auth",
                "auth.denied",
                {"path": path, "status": 403, "reason": "suspended"},
                agent=agent,
            )
            return agent, JSONResponse(error_body("pass suspended", "permission_error"), 403)
        return agent, None

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/v1/models")
        async def list_models(request: Request) -> Response:
            agent, denied = self._denied(request, "/v1/models")
            if denied is not None or agent is None:
                return denied or JSONResponse({}, 401)
            model = self.settings.policy.agents[agent].model
            return JSONResponse(
                {
                    "object": "list",
                    "data": [{"id": model, "object": "model", "created": 0, "owned_by": "steward"}],
                }
            )

        @router.post("/v1/chat/completions")
        async def chat_completions(request: Request) -> Response:
            return await self.chat(request)

        return router

    async def chat(self, request: Request) -> Response:
        started = time.perf_counter()
        path = "/v1/chat/completions"
        agent, denied = self._denied(request, path)
        if denied is not None or agent is None:
            return denied or JSONResponse({}, 401)
        policy = self.settings.policy
        model = policy.agents[agent].model
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self._record(agent, model, started, status=400, stream=False)
            return JSONResponse(
                error_body("body must be a JSON object", "invalid_request_error"), 400
            )

        refused = self.over_budget(agent, model)
        if refused is not None:
            return refused
        body, mask_fired = mask_body(body, policy.mask)
        # The pass decides the model, not the agent.
        body["model"] = model
        stream = body.get("stream") is True
        if stream:
            # D9: always ask for the final usage chunk so the call can be metered.
            options = body.get("stream_options")
            body["stream_options"] = {
                **(options if isinstance(options, dict) else {}),
                "include_usage": True,
            }
        headers = {
            "Authorization": f"Bearer {self.settings.nebius_api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            # The agent's Accept-Encoding is not forwarded; identity keeps raw bytes relayable.
            "Accept-Encoding": "identity",
        }
        assert self.client is not None, "model door used before startup"
        upstream_request = self.client.build_request(
            "POST", f"{self.settings.upstream_url}/chat/completions", json=body, headers=headers
        )
        a2a_request = self.active_request(agent)
        try:
            upstream = await self.client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            self._record(
                agent,
                model,
                started,
                status=502,
                stream=stream,
                mask_fired=mask_fired,
                a2a_request=a2a_request,
                error=type(exc).__name__,
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
            self._record(
                agent,
                model,
                started,
                status=upstream.status_code,
                stream=False,
                usage=usage,
                tool_calls=calls,
                mask_fired=mask_fired,
                a2a_request=a2a_request,
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
                self._record(
                    agent,
                    model,
                    started,
                    status=upstream.status_code,
                    stream=True,
                    usage=scanner.usage,
                    tool_calls=scanner.tool_calls,
                    mask_fired=mask_fired,
                    a2a_request=a2a_request,
                )

        return StreamingResponse(
            relay(), upstream.status_code, relayed, media_type or "text/event-stream"
        )

    def _record(
        self,
        agent: str,
        model: str,
        started: float,
        *,
        status: int,
        stream: bool,
        usage: Any = None,
        tool_calls: list[str] | None = None,
        mask_fired: list[int] | None = None,
        a2a_request: str | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "model": model,
            "stream": stream,
            "status": status,
            "latency_ms": round((time.perf_counter() - started) * 1000) if started else 0,
            "usage": usage_tokens(usage),
            "cost_usd": call_cost(usage, self.settings.policy.prices.get(model)),
            "tool_calls": tool_calls or [],
            "mask_fired": mask_fired or [],
        }
        if a2a_request:
            payload["a2a_request"] = a2a_request
        if error:
            payload["error"] = error
        self.recorder.record("model", "model.call", payload, agent=agent)
