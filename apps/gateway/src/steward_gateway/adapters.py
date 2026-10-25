"""How Steward starts a turn of an agent: to answer a request, or to pass on a note.

The answering agent gets one message, rendered by Steward from typed fields, and answers by
calling Steward's reply tool. The adapter only starts the agent's turn and reports how the run
ended; the reply itself arrives through the tool door. Phase 2 adds Hermes runs, used for change
notices and for telling the work agent that a held call was decided.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass
class DeliveryResult:
    ok: bool
    status: str
    duration_ms: int
    exit_code: int | None = None
    usage: dict[str, Any] | None = None
    tools: list[str] = field(default_factory=list)
    final: str | None = None
    error: str | None = None

    def log_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
            "usage": self.usage,
            "tools": self.tools,
            # The answering agent's closing text stays in the owner's log only; it never crosses.
            "final": (self.final or "")[:500] or None,
            "error": self.error,
        }


class Adapter(Protocol):
    async def deliver(self, request_id: str, message: str) -> DeliveryResult: ...


class OpenClawExecAdapter:
    """Runs one OpenClaw turn inside the agent's OpenShell sandbox (Phase 1 D4):

        openshell sandbox exec -n <sandbox> -- openclaw agent exec <message> --json --timeout <s>

    `agent exec` runs one embedded turn without a Gateway in a fresh temporary state directory,
    so nothing carries over between requests. Exit codes: 0 ok, 1 error, 2 timeout.
    `openshell sandbox exec` reads stdin, so the subprocess gets none.
    """

    def __init__(
        self, sandbox: str, *, timeout_s: int = 120, command: Sequence[str] = ("openshell",)
    ) -> None:
        self.sandbox = sandbox
        self.timeout_s = timeout_s
        self.command = list(command)

    def argv(self, message: str) -> list[str]:
        return [
            *self.command,
            "sandbox",
            "exec",
            "-n",
            self.sandbox,
            "--",
            "openclaw",
            "agent",
            "exec",
            message,
            "--json",
            "--timeout",
            str(self.timeout_s),
        ]

    async def deliver(self, request_id: str, message: str) -> DeliveryResult:
        started = time.perf_counter()
        try:
            process = await asyncio.create_subprocess_exec(
                *self.argv(message),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return DeliveryResult(False, "not_started", _ms(started), error=str(exc))
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout_s + 30)
        except TimeoutError:
            process.kill()
            await process.wait()
            return DeliveryResult(False, "killed", _ms(started), error="no exit after timeout")
        envelope = _last_json_object(stdout.decode("utf-8", "replace"))
        if envelope is None:
            tail = stderr.decode("utf-8", "replace").strip()[-300:]
            return DeliveryResult(
                False, "no_json", _ms(started), process.returncode, error=tail or None
            )
        summary = envelope.get("toolSummary") or {}
        error = envelope.get("error")
        return DeliveryResult(
            ok=process.returncode == 0 and envelope.get("ok") is True,
            status=str(envelope.get("status")),
            duration_ms=_ms(started),
            exit_code=process.returncode,
            usage=envelope.get("usage") if isinstance(envelope.get("usage"), dict) else None,
            tools=[str(t) for t in summary.get("tools") or []] if isinstance(summary, dict) else [],
            final=envelope.get("final") if isinstance(envelope.get("final"), str) else None,
            error=json.dumps(error) if error else None,
        )


class HermesRunsAdapter:
    """Starts a Hermes run through its API server (Phase 2: change notices and the result of a
    decided hold, P2-D9 option B), then polls the run until it ends:

        POST {url}/v1/runs {"input": <message>, "session_id": <session>}
        GET  {url}/v1/runs/{run_id}

    On the VM the API server is reached through the OpenShell forward on 127.0.0.1:8642 with
    HERMES_API_KEY. Runs for one agent are serialised, so two notes never overlap in a session.
    """

    TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})

    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        timeout_s: int = 600,
        session_id: str = "steward",
        transport: httpx.AsyncBaseTransport | None = None,
        poll_s: float = 2.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.session_id = session_id
        self.transport = transport
        self.poll_s = poll_s
        self._lock = asyncio.Lock()

    async def deliver(self, request_id: str, message: str) -> DeliveryResult:
        async with self._lock:
            return await self._run(request_id, message)

    async def _run(self, request_id: str, message: str) -> DeliveryResult:
        started = time.perf_counter()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        try:
            async with httpx.AsyncClient(
                base_url=self.url, headers=headers, timeout=timeout, transport=self.transport
            ) as client:
                response = await client.post(
                    "/v1/runs",
                    json={"input": message, "session_id": self.session_id},
                    headers={"Idempotency-Key": request_id},
                )
                if response.status_code >= 400:
                    return DeliveryResult(
                        False, "not_started", _ms(started), error=f"HTTP {response.status_code}"
                    )
                body = response.json()
                run_id = body.get("run_id") or body.get("id")
                if not run_id:
                    return DeliveryResult(False, "no_run_id", _ms(started))
                deadline = time.monotonic() + self.timeout_s
                run: dict[str, Any] = {}
                while time.monotonic() < deadline:
                    polled = await client.get(f"/v1/runs/{run_id}")
                    run = polled.json() if polled.status_code < 400 else {}
                    if run.get("status") in self.TERMINAL:
                        break
                    await asyncio.sleep(self.poll_s)
                status = str(run.get("status") or "timeout")
                output = run.get("output")
                return DeliveryResult(
                    ok=status == "completed",
                    status=status,
                    duration_ms=_ms(started),
                    usage=run.get("usage") if isinstance(run.get("usage"), dict) else None,
                    final=output if isinstance(output, str) else None,
                )
        except httpx.HTTPError as exc:
            return DeliveryResult(False, "unreachable", _ms(started), error=type(exc).__name__)


class MockAdapter:
    """Scripted answering agent for tests (and, later, replay): calls responder in the
    background the way a real agent would, through the tool door."""

    def __init__(self, responder: Callable[[str, str], Awaitable[None]]) -> None:
        self.responder = responder
        self.messages: list[str] = []

    async def deliver(self, request_id: str, message: str) -> DeliveryResult:
        started = time.perf_counter()
        self.messages.append(message)
        try:
            await self.responder(request_id, message)
        except Exception as exc:
            return DeliveryResult(False, "error", _ms(started), error=repr(exc))
        return DeliveryResult(True, "ok", _ms(started), exit_code=0)


def _last_json_object(text: str) -> dict[str, Any] | None:
    """The JSON envelope: the whole of stdout, or else the last line that parses as an object."""
    lines = text.strip().splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("{")]
    # Whole output, then from each line that opens an object (pretty-printed after noise), then
    # single lines from the end.
    candidates = [text.strip(), *("\n".join(lines[i:]) for i in starts), *reversed(lines)]
    for candidate in candidates:
        if candidate.startswith("{"):
            try:
                value = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    return None


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
