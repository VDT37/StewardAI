"""The agent-to-agent door (ADR 0002): typed asks, replies trimmed by code.

ask (work side) runs the seven steps: check the request, forward a message rendered from the
typed fields only, wait for the answering agent to call reply, check and trim the reply, return
what is allowed to cross, and log every step. If no reply arrives in time, nothing is shared.

Open requests live in memory (request id -> future), which is why Steward runs one worker.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool
from pydantic import BaseModel

from steward_core import availability
from steward_core.checker import Ask, CheckContext, Checker, Decision
from steward_core.clock import london_day, utc_now
from steward_core.policy import ContactRule, Policy, exposed_name
from steward_core.store import Store
from steward_gateway.adapters import Adapter
from steward_gateway.recorder import Recorder

STATUS_TEXT = {
    "refused": "Refused by Steward",
    "held": "Held by Steward for the owner",
    "no_answer": "No answer",
}


def result(text: str, data: dict[str, Any], *, error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)], structured_content=data, is_error=error
    )


@dataclass
class Pending:
    request_id: str
    asker: str
    contact: str
    request_type: str
    request: BaseModel
    route: ContactRule
    asks_today: int
    future: asyncio.Future[ReplyOutcome]
    created: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class ReplyOutcome:
    status: str  # answered, held or no_answer
    reply: BaseModel | None = None
    reason: str | None = None


class AgentToAgentDoor:
    def __init__(
        self,
        policy: Policy,
        store: Store,
        recorder: Recorder,
        checker: Checker,
        adapters: Mapping[str, Adapter],
        window: tuple[dt.date, dt.date] | None = None,
    ) -> None:
        self.policy = policy
        self.store = store
        self.recorder = recorder
        self.checker = checker
        self.adapters = adapters
        self.window = window
        self.pending: dict[str, Pending] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    # Tool definitions -------------------------------------------------------------------------

    def ask_tool(self, agent: str) -> Tool:
        contacts = [rule.contact for rule in self.policy.contacts_of(agent)]
        types = sorted({t for rule in self.policy.contacts_of(agent) for t in rule.request_types})
        lines = [availability.ASK_DESCRIPTION] if "availability" in types else []
        return Tool(
            name="ask",
            description=(
                "Ask another of the owner's agents something through Steward. Steward checks the "
                "request, forwards only the typed fields, and returns the trimmed answer. "
                f"contact: one of {contacts}. request_type: one of {types}; any other type is "
                "refused and still counts towards the daily limit. Known types:\n"
                + "\n".join(lines)
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "contact": {"type": "string", "description": f"One of {contacts}."},
                    "request_type": {"type": "string", "description": f"One of {types}."},
                    "fields": {"type": "object", "description": "The request type's fields."},
                },
                "required": ["contact", "request_type", "fields"],
            },
        )

    def reply_tool(self) -> Tool:
        return Tool(
            name="reply",
            description=(
                "Answer a Steward request addressed to you. request_id: the id in the request "
                "message. fields: the reply shape of the request's type.\n"
                + availability.REPLY_DESCRIPTION
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "fields": {"type": "object"},
                },
                "required": ["request_id", "fields"],
            },
        )

    def active_request(self, agent: str) -> str | None:
        """The newest open request this agent is answering (to tag its calls in the log)."""
        mine = [p for p in self.pending.values() if p.contact == agent]
        return max(mine, key=lambda p: p.created).request_id if mine else None

    # ask --------------------------------------------------------------------------------------

    async def ask(self, asker: str, arguments: Mapping[str, Any]) -> CallToolResult:
        contact = str(arguments.get("contact", ""))
        request_type = str(arguments.get("request_type", ""))
        fields = arguments.get("fields")
        route = self.policy.route(asker, contact)
        day = london_day(utc_now()).isoformat()
        count = self.store.bump_ask_count(day, asker, contact)
        ctx = CheckContext(asker, contact, route, count, self.window)
        check = await self.checker.check_request(ctx, Ask(contact, request_type, fields))
        base = {
            "contact": contact,
            "request_type": request_type,
            "asks_today": count,
            "daily_limit": route.daily_limit if route else None,
        }
        if not check.verdict.allowed or check.request is None or route is None:
            held = check.verdict.decision is Decision.HOLD
            status = "held" if held else "refused"
            self.recorder.record(
                "a2a",
                "a2a.held" if held else "a2a.refused",
                {
                    **base,
                    "fields": fields,
                    "rule": check.verdict.rule,
                    "reason": check.verdict.reason,
                },
                agent=asker,
            )
            data = {"request_id": None, "status": status, "reason": check.verdict.reason}
            return result(f"{STATUS_TEXT[status]}: {check.verdict.reason}", data, error=True)

        request_id = f"r-{secrets.token_hex(6)}"
        self.recorder.record(
            "a2a",
            "a2a.request",
            {**base, "request_id": request_id, "fields": check.request.model_dump(mode="json")},
            agent=asker,
        )
        adapter = self.adapters.get(contact)
        if adapter is None:
            return self._no_answer(asker, request_id, "the contact has no delivery configured")
        future: asyncio.Future[ReplyOutcome] = asyncio.get_running_loop().create_future()
        pending = Pending(
            request_id, asker, contact, request_type, check.request, route, count, future
        )
        self.pending[request_id] = pending
        message = self.render(pending)
        task = asyncio.create_task(self._deliver(pending, adapter, message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            outcome = await asyncio.wait_for(
                asyncio.shield(future), self.policy.a2a.reply_timeout_s
            )
        except TimeoutError:
            self.recorder.record(
                "a2a",
                "a2a.timeout",
                {"request_id": request_id, "waited_s": self.policy.a2a.reply_timeout_s},
                agent=asker,
            )
            return self._answer(request_id, "no_answer", "no reply in time; nothing was shared")
        finally:
            self.pending.pop(request_id, None)
        if outcome.status == "answered" and outcome.reply is not None:
            assert isinstance(check.request, availability.AvailabilityRequest)
            assert isinstance(outcome.reply, availability.AvailabilityReplyOut)
            summary = availability.describe_reply(check.request, outcome.reply)
            answered = {
                "request_id": request_id,
                "status": "answered",
                "reply": outcome.reply.model_dump(mode="json"),
                "summary": summary,
            }
            return result(f"Answer from {contact} via Steward. {summary}", answered)
        return self._answer(request_id, outcome.status, outcome.reason or "nothing was shared")

    def render(self, pending: Pending) -> str:
        contact_rules = self.policy.agents[pending.contact]
        prefix = contact_rules.tool_prefix
        calendar = next(
            (
                exposed_name(t)
                for t in contact_rules.tools
                if exposed_name(t) == "calendar_list_events"
            ),
            None,
        )
        assert isinstance(pending.request, availability.AvailabilityRequest)
        return availability.request_message(
            pending.request_id,
            pending.asker,
            pending.request,
            calendar_tool=f"{prefix}{calendar}" if calendar else None,
            reply_tool=f"{prefix}reply",
        )

    async def _deliver(self, pending: Pending, adapter: Adapter, message: str) -> None:
        try:
            delivery = await adapter.deliver(pending.request_id, message)
            payload = {"request_id": pending.request_id, **delivery.log_payload()}
        except Exception as exc:
            payload = {"request_id": pending.request_id, "ok": False, "error": repr(exc)}
        self.recorder.record("a2a", "a2a.delivery", payload, agent=pending.contact)
        if pending.future.done():
            return
        # The run ended; give a reply that is still in flight a moment, then stop waiting.
        await asyncio.sleep(self.policy.a2a.reply_grace_s)
        if not pending.future.done():
            self.recorder.record(
                "a2a", "a2a.no_reply", {"request_id": pending.request_id}, agent=pending.contact
            )
            pending.future.set_result(
                ReplyOutcome("no_answer", reason="the contact's agent ended without replying")
            )

    def _no_answer(self, asker: str, request_id: str, reason: str) -> CallToolResult:
        self.recorder.record(
            "a2a", "a2a.no_reply", {"request_id": request_id, "reason": reason}, agent=asker
        )
        return self._answer(request_id, "no_answer", reason)

    def _answer(self, request_id: str, status: str, reason: str) -> CallToolResult:
        data = {"request_id": request_id, "status": status, "reason": reason}
        return result(f"{STATUS_TEXT.get(status, status)}: {reason}", data, error=True)

    # reply ------------------------------------------------------------------------------------

    async def reply(self, agent: str, arguments: Mapping[str, Any]) -> CallToolResult:
        request_id = str(arguments.get("request_id", ""))
        fields = arguments.get("fields")
        pending = self.pending.get(request_id)
        if pending is None or pending.contact != agent or pending.future.done():
            self.recorder.record(
                "a2a",
                "a2a.reply_rejected",
                {"request_id": request_id, "reason": "no open request with this id for you"},
                agent=agent,
            )
            return result(
                "No open request with that id is addressed to you.",
                {"accepted": False},
                error=True,
            )
        ctx = CheckContext(pending.asker, agent, pending.route, pending.asks_today, self.window)
        check = await self.checker.check_reply(ctx, pending.request_type, pending.request, fields)
        if check.verdict.decision is Decision.REFUSE:
            # The reply does not fit the type. Nothing crosses; the agent may try again.
            self.recorder.record(
                "a2a",
                "a2a.reply_invalid",
                {
                    "request_id": request_id,
                    "fields": fields,
                    "rule": check.verdict.rule,
                    "reason": check.verdict.reason,
                },
                agent=agent,
            )
            return result(
                f"Reply not accepted: {check.verdict.reason}. Fix the fields and call reply again.",
                {"accepted": False},
                error=True,
            )
        if check.verdict.decision is Decision.HOLD or check.reply is None:
            self.recorder.record(
                "a2a",
                "a2a.held",
                {
                    "request_id": request_id,
                    "fields": fields,
                    "rule": check.verdict.rule,
                    "reason": check.verdict.reason,
                },
                agent=agent,
            )
            pending.future.set_result(ReplyOutcome("held", reason=check.verdict.reason))
            return result("Reply received.", {"accepted": True})
        self.recorder.record(
            "a2a",
            "a2a.reply",
            {
                "request_id": request_id,
                "asker": pending.asker,
                "request_type": pending.request_type,
                "reply_in": fields,
                "reply_out": check.reply.model_dump(mode="json"),
                "rules_fired": check.rules_fired,
                "dropped_fields": check.dropped_fields,
                "detail_minutes": pending.route.detail_minutes,
            },
            agent=agent,
        )
        pending.future.set_result(ReplyOutcome("answered", check.reply))
        # The answering agent is not told what was trimmed.
        return result("Reply received.", {"accepted": True})
