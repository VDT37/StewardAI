"""How Steward hands an agent-to-agent request to the answering agent.

The answering agent gets one message, rendered by Steward from typed fields, and answers by
calling Steward's reply tool. The adapter only starts the agent's turn and reports how the run
ended; the reply itself arrives through the tool door.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


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
