"""The agent-to-agent door (ADR 0002): typed asks, replies trimmed by code, change notices.

ask (work side) runs the seven steps: check the request, forward a message rendered from the
typed fields only, wait for the answering agent to call reply, check and trim the reply, return
what is allowed to cross, and log every step. If no reply arrives in time, nothing is shared.

Phase 2: four request types (availability, travel_limits, needs, address_for_booking). Asks over
the daily limit or in a burst, and every address request, are held for the owner; an approved
ask then runs, and an approved address request is answered by Steward with a stand-in such as
[ADDR-1]. When an answered availability window changes on the contact's calendar, Steward
computes a change notice and passes it to the asker (P2-D1 option A).

Open requests live in memory (request id -> future), which is why Steward runs one worker.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool
from pydantic import BaseModel, ValidationError

from steward_core import address, availability
from steward_core.address import ADDRESS_TOOLS, StandIn
from steward_core.checker import Ask, CheckContext, Checker, Decision, parse_fields
from steward_core.clock import Clock, iso_ms, london_day, parse_iso
from steward_core.notices import ChangeNotice, Watch, diff, notice_message, recompute
from steward_core.policy import ContactRule, Policy, exposed_name
from steward_core.request_types import REQUEST_TYPES, request_message
from steward_core.store import Hold, Store
from steward_fixtures import load_raw
from steward_gateway.adapters import Adapter
from steward_gateway.holds import HoldBook, short_fingerprint
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
    status: str  # answered, held, no_answer or timeout
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
        holds: HoldBook,
        clock: Clock,
        window: tuple[dt.date, dt.date] | None = None,
    ) -> None:
        self.policy = policy
        self.store = store
        self.recorder = recorder
        self.checker = checker
        self.adapters = adapters
        self.holds = holds
        self.clock = clock
        self.window = window
        self.pending: dict[str, Pending] = {}
        self.watches: list[Watch] = []
        self._asks: dict[tuple[str, str, str], deque[dt.datetime]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        # Set by the app: reads a contact's calendar for change notices (upstream tool id, args).
        self.read_calendar: Any = None
        holds.executors["ask"] = self.execute_held_ask
        holds.executors["address"] = self.issue_stand_in

    def reset(self) -> None:
        """Forget watches and burst windows (a scenario run starts clean)."""
        self.watches.clear()
        self._asks.clear()

    def _spawn(self, coro: Any) -> None:
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # Tool definitions -------------------------------------------------------------------------

    def ask_tool(self, agent: str) -> Tool:
        contacts = [rule.contact for rule in self.policy.contacts_of(agent)]
        types = sorted({t for rule in self.policy.contacts_of(agent) for t in rule.request_types})
        lines = [REQUEST_TYPES[t].ask_description for t in types if t in REQUEST_TYPES]
        return Tool(
            name="ask",
            description=(
                "Ask another of the owner's agents something through Steward. Steward checks the "
                "request, forwards only the typed fields, and returns the trimmed answer. "
                f"contact: one of {contacts}. request_type: one of {types}; any other type is "
                "refused and still counts towards the daily limit. Some asks are held for the "
                "owner and return a hold id; check those later with hold_status. Known types:\n"
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
        shapes = [
            t.reply_description for t in REQUEST_TYPES.values() if t.reply_description is not None
        ]
        return Tool(
            name="reply",
            description=(
                "Answer a Steward request addressed to you. request_id: the id in the request "
                "message. fields: the reply shape of the request's type.\n" + "\n".join(shapes)
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

    def _burst(self, asker: str, contact: str, now: dt.datetime, route: ContactRule | None) -> int:
        key = (self.recorder.scope, asker, contact)
        times = self._asks.setdefault(key, deque())
        times.append(now)
        minutes = route.burst_minutes if route else 10
        while times and times[0] <= now - timedelta(minutes=minutes):
            times.popleft()
        return len(times)

    async def ask(self, asker: str, arguments: Mapping[str, Any]) -> CallToolResult:
        now = self.clock.now()
        contact = str(arguments.get("contact", ""))
        request_type = str(arguments.get("request_type", ""))
        fields = arguments.get("fields")
        route = self.policy.route(asker, contact)
        day = london_day(now).isoformat()
        count = self.store.bump_ask_count(day, asker, contact, self.recorder.scope)
        burst = self._burst(asker, contact, now, route)
        ctx = CheckContext(asker, contact, route, count, self.window, burst)
        check = await self.checker.check_request(ctx, Ask(contact, request_type, fields))
        base = {
            "call": {"tool": "ask", "arguments": dict(arguments)},
            "contact": contact,
            "request_type": request_type,
            "asks_today": count,
            "daily_limit": route.daily_limit if route else None,
        }
        decision = check.verdict.decision
        if decision is Decision.HOLD and check.request is not None and route is not None:
            return self._hold_ask(asker, request_type, check, base, now)
        if not check.verdict.allowed or check.request is None or route is None:
            held = decision is Decision.HOLD
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
                at=now,
            )
            data = {
                "request_id": None,
                "status": status,
                "rule": check.verdict.rule,
                "reason": check.verdict.reason,
            }
            return result(f"{STATUS_TEXT[status]}: {check.verdict.reason}", data, error=True)
        outcome, request_id = await self._exchange(
            asker, contact, request_type, check.request, route, count, base, now
        )
        return self._answer_result(contact, request_type, check.request, outcome, request_id)

    def _hold_ask(
        self,
        asker: str,
        request_type: str,
        check: Any,
        base: dict[str, Any],
        now: dt.datetime,
    ) -> CallToolResult:
        request: BaseModel = check.request
        fields = request.model_dump(mode="json")
        kind = "address" if request_type == "address_for_booking" else "ask"
        details: dict[str, Any] = {"asks_today": base["asks_today"], "limit": base["daily_limit"]}
        if isinstance(request, address.AddressRequest):
            details = {"card": address.card_text(request, _taxi_price()), "request": fields}
        args = {"contact": base["contact"], "request_type": request_type, "fields": fields}
        hold = self.holds.create(
            agent=asker,
            kind=kind,
            tool="ask",
            tool_id=None,
            args=args,
            rule=check.verdict.rule,
            rules=[check.verdict.rule],
            reason=check.verdict.reason,
            details=details,
            now=now,
        )
        self.recorder.record(
            "a2a",
            "a2a.held",
            {
                **base,
                "fields": fields,
                "rule": check.verdict.rule,
                "reason": check.verdict.reason,
                "hold_id": hold.hold_id,
                "fingerprint": hold.fingerprint,
            },
            agent=asker,
            at=now,
        )
        data = {
            "request_id": None,
            "status": "held",
            "hold_id": hold.hold_id,
            "rule": hold.rule,
            "reason": hold.reason,
            "fingerprint": short_fingerprint(hold.fingerprint),
            "message": "Held for the owner. Do not retry; check later with hold_status.",
        }
        text = (
            f"Held by Steward for the owner (hold {hold.hold_id}, rule {hold.rule}): "
            f"{hold.reason}. Do not retry; check later with hold_status."
        )
        return result(text, data)

    async def _exchange(
        self,
        asker: str,
        contact: str,
        request_type: str,
        request: BaseModel,
        route: ContactRule,
        count: int,
        base: dict[str, Any],
        now: dt.datetime,
        hold_id: str | None = None,
    ) -> tuple[ReplyOutcome, str]:
        """Forward one checked request and wait for the trimmed reply."""
        request_id = self.recorder.ids.next("r")
        payload = {**base, "request_id": request_id, "fields": request.model_dump(mode="json")}
        if hold_id is not None:
            payload["hold_id"] = hold_id
        self.recorder.record("a2a", "a2a.request", payload, agent=asker, at=now)
        adapter = self.adapters.get(contact)
        if adapter is None:
            reason = "the contact has no delivery configured"
            self.recorder.record(
                "a2a", "a2a.no_reply", {"request_id": request_id, "reason": reason}, agent=asker
            )
            return ReplyOutcome("no_answer", reason=reason), request_id
        future: asyncio.Future[ReplyOutcome] = asyncio.get_running_loop().create_future()
        pending = Pending(request_id, asker, contact, request_type, request, route, count, future)
        self.pending[request_id] = pending
        message = self.render(pending)
        self._spawn(self._deliver(pending, adapter, message))
        try:
            outcome = await asyncio.wait_for(
                asyncio.shield(future), self.policy.a2a.reply_timeout_s
            )
        except TimeoutError:
            outcome = ReplyOutcome("timeout")
        finally:
            self.pending.pop(request_id, None)
        if outcome.status == "timeout":
            self.recorder.record(
                "a2a",
                "a2a.timeout",
                {"request_id": request_id, "waited_s": self.policy.a2a.reply_timeout_s},
                agent=asker,
            )
            return ReplyOutcome("no_answer", reason="no reply in time; nothing was shared"), (
                request_id
            )
        if outcome.status == "answered" and isinstance(request, availability.AvailabilityRequest):
            assert isinstance(outcome.reply, availability.AvailabilityReplyOut)
            self.watches.append(
                Watch(
                    request_id,
                    asker,
                    contact,
                    request,
                    route.detail_minutes,
                    list(outcome.reply.blocks),
                )
            )
        return outcome, request_id

    def force_timeout(self, request_id: str) -> None:
        """Replay: end the wait for a request the way the recorded run's timeout did."""
        pending = self.pending.get(request_id)
        if pending is not None and not pending.future.done():
            pending.future.set_result(ReplyOutcome("timeout"))

    def _answer_result(
        self,
        contact: str,
        request_type: str,
        request: BaseModel,
        outcome: ReplyOutcome,
        request_id: str,
    ) -> CallToolResult:
        if outcome.status == "answered" and outcome.reply is not None:
            data = self._answer_data(contact, request_type, request, outcome.reply, request_id)
            return result(f"Answer from {contact} via Steward. {data['summary']}", data)
        return self._answer(request_id, outcome.status, outcome.reason or "nothing was shared")

    def _answer_data(
        self, contact: str, request_type: str, request: BaseModel, reply: BaseModel, request_id: str
    ) -> dict[str, Any]:
        describe = REQUEST_TYPES[request_type].describe
        summary = describe(request, reply) if describe else ""
        return {
            "request_id": request_id,
            "status": "answered",
            "reply": reply.model_dump(mode="json"),
            "summary": summary,
        }

    def render(self, pending: Pending) -> str:
        contact_rules = self.policy.agents[pending.contact]
        prefix = contact_rules.tool_prefix
        rtype = REQUEST_TYPES[pending.request_type]
        exposed = {exposed_name(t) for t in contact_rules.tools}
        read_tool = (
            f"{prefix}{rtype.read_tool}" if rtype.read_tool and rtype.read_tool in exposed else None
        )
        return request_message(
            rtype,
            pending.request_id,
            pending.asker,
            pending.request,
            read_tool=read_tool,
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

    def _answer(self, request_id: str, status: str, reason: str) -> CallToolResult:
        data = {"request_id": request_id, "status": status, "reason": reason}
        return result(f"{STATUS_TEXT.get(status, status)}: {reason}", data, error=True)

    # Held asks and address stand-ins (executors for the hold book) ----------------------------

    async def execute_held_ask(self, hold: Hold, choice: str) -> tuple[str, dict[str, Any]]:
        args = hold.args
        contact, request_type = str(args["contact"]), str(args["request_type"])
        route = self.policy.route(hold.agent, contact)
        rtype = REQUEST_TYPES.get(request_type)
        if route is None or rtype is None:
            return "failed", {"error": "the route or request type no longer exists"}
        try:
            request = parse_fields(rtype.request_model, args["fields"])
        except (TypeError, ValidationError):
            return "failed", {"error": "the held request no longer validates"}
        base = {
            "contact": contact,
            "request_type": request_type,
            "asks_today": hold.details.get("asks_today"),
            "daily_limit": route.daily_limit,
        }
        outcome, request_id = await self._exchange(
            hold.agent,
            contact,
            request_type,
            request,
            route,
            int(hold.details.get("asks_today") or 0),
            base,
            self.clock.now(),
            hold_id=hold.hold_id,
        )
        if outcome.status == "answered" and outcome.reply is not None:
            return "done", self._answer_data(
                contact, request_type, request, outcome.reply, request_id
            )
        return "failed", {"request_id": request_id, "status": outcome.status}

    async def issue_stand_in(self, hold: Hold, choice: str) -> tuple[str, dict[str, Any]]:
        # The approval's moment, so the expiry is the same in a replay.
        now = parse_iso(hold.decided) if hold.decided else self.clock.now()
        fields = hold.args["fields"]
        token = f"[ADDR-{self.recorder.ids.number('ADDR')}]"
        binding = StandIn(
            token=token,
            agent=hold.agent,
            hold_id=hold.hold_id,
            tools=ADDRESS_TOOLS,
            trip_date=str(fields["trip_date"]),
            expires=iso_ms(now + timedelta(hours=self.policy.holds.stand_in_hours)),
        )
        self.store.save_stand_in(self.recorder.session_id, token, binding.as_dict())
        self.recorder.record(
            "a2a",
            "stand_in.issued",
            {"hold_id": hold.hold_id, **binding.as_dict()},
            agent=hold.agent,
            at=now,
        )
        message = (
            f"Use {token} as the address in one travel_book_taxi call for {binding.trip_date}. "
            "Steward fills in the real address when the booking goes out. Use it nowhere else."
        )
        return "done", {"address": token, "trip_date": binding.trip_date, "message": message}

    # reply ------------------------------------------------------------------------------------

    async def reply(self, agent: str, arguments: Mapping[str, Any]) -> CallToolResult:
        now = self.clock.now()
        request_id = str(arguments.get("request_id", ""))
        fields = arguments.get("fields")
        call = {"tool": "reply", "arguments": dict(arguments)}
        pending = self.pending.get(request_id)
        if pending is None or pending.contact != agent or pending.future.done():
            self.recorder.record(
                "a2a",
                "a2a.reply_rejected",
                {
                    "call": call,
                    "request_id": request_id,
                    "reason": "no open request with this id for you",
                },
                agent=agent,
                at=now,
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
                    "call": call,
                    "request_id": request_id,
                    "fields": fields,
                    "rule": check.verdict.rule,
                    "reason": check.verdict.reason,
                },
                agent=agent,
                at=now,
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
                    "call": call,
                    "request_id": request_id,
                    "fields": fields,
                    "rule": check.verdict.rule,
                    "reason": check.verdict.reason,
                },
                agent=agent,
                at=now,
            )
            pending.future.set_result(ReplyOutcome("held", reason=check.verdict.reason))
            return result("Reply received.", {"accepted": True})
        self.recorder.record(
            "a2a",
            "a2a.reply",
            {
                "call": call,
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
            at=now,
        )
        pending.future.set_result(ReplyOutcome("answered", check.reply))
        # The answering agent is not told what was trimmed.
        return result("Reply received.", {"accepted": True})

    # Change notices ---------------------------------------------------------------------------

    async def check_notices(self, side: str) -> list[str]:
        """Recompute every watch on this side's calendar and send each asker one notice with
        the slots that changed (a change seen by several answered windows is sent once).
        Returns the notice ids sent."""
        sent: list[str] = []
        if self.read_calendar is None:
            return sent
        found: dict[str, list[tuple[Watch, list[ChangeNotice]]]] = {}
        for watch in [w for w in self.watches if w.contact == side]:
            events = await self.read_calendar(
                watch.contact,
                watch.request.start_date.isoformat(),
                watch.request.end_date.isoformat(),
            )
            if events is None:
                continue
            fresh = recompute(watch, events)
            notices = diff(watch.blocks, fresh, watch.grid)
            watch.blocks = fresh
            if notices:
                found.setdefault(watch.asker, []).append((watch, notices))
        for asker, items in found.items():
            unique: dict[str, ChangeNotice] = {}
            for _, notices in items:
                for notice in notices:
                    unique.setdefault(notice.model_dump_json(), notice)
            changes = sorted(unique.values(), key=lambda n: (n.date, n.start, n.change))
            request_ids = [watch.request_id for watch, _ in items]
            notice_id = self.recorder.ids.next("n")
            self.recorder.record(
                "a2a",
                "a2a.notice",
                {
                    "notice_id": notice_id,
                    "request_ids": request_ids,
                    "asker": asker,
                    "from_side": side,
                    "notices": [n.model_dump(mode="json") for n in changes],
                },
                agent=side,
            )
            message = notice_message(notice_id, side, request_ids, changes)
            adapter = self.adapters.get(asker)
            if adapter is not None:
                self._spawn(self._send_notice(asker, notice_id, adapter, message))
            sent.append(notice_id)
        return sent

    async def _send_notice(
        self, asker: str, notice_id: str, adapter: Adapter, message: str
    ) -> None:
        try:
            delivery = await adapter.deliver(notice_id, message)
            payload = {"notice_id": notice_id, **delivery.log_payload()}
        except Exception as exc:
            payload = {"notice_id": notice_id, "ok": False, "error": repr(exc)}
        self.recorder.record("a2a", "a2a.notice_delivery", payload, agent=asker)

    async def tell(self, agent: str, key: str, message: str) -> None:
        """Start a run of an agent with a message from Steward (P2-D9 B: a decided hold)."""
        adapter = self.adapters.get(agent)
        if adapter is None:
            return
        self._spawn(self._send_note(agent, key, adapter, message))

    async def _send_note(self, agent: str, key: str, adapter: Adapter, message: str) -> None:
        try:
            delivery = await adapter.deliver(key, message)
            payload = {"key": key, **delivery.log_payload()}
        except Exception as exc:
            payload = {"key": key, "ok": False, "error": repr(exc)}
        self.recorder.record("a2a", "hold.told", payload, agent=agent)


def _taxi_price() -> int | None:
    taxis = load_raw("travel_options").get("taxis") or []
    price = taxis[0].get("price_gbp") if taxis else None
    return int(price) if isinstance(price, int) else None
