"""The checker interface (ADR 0005) and the deterministic checker used in the product.

A checker looks at an ask before it is forwarded and at a reply before it crosses, and returns
a verdict: allow, refuse or hold, plus the rule that fired. FailClosed wraps any checker so an
error or a timeout becomes a hold, never an allow. Jev and the Nemotron judge are stubs until
the Phase 4 comparison.

Phase 2: asks over the daily limit or in a burst, and every address request, are held for the
owner instead of refused, and every request type in request_types goes through the same checks.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from steward_core import address, availability
from steward_core.policy import ContactRule
from steward_core.request_types import REQUEST_TYPES


class Decision(StrEnum):
    ALLOW = "allow"
    REFUSE = "refuse"
    HOLD = "hold"


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    rule: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


@dataclass(frozen=True)
class CheckContext:
    asker: str
    contact: str
    route: ContactRule | None
    # This ask's number for the day, counting refused and held asks.
    asks_today: int
    # Dates a request may cover (the demo window); None means no limit.
    window: tuple[dt.date, dt.date] | None = None
    # Asks to this contact within the route's burst window, this one included.
    asks_in_burst: int = 1


@dataclass(frozen=True)
class Ask:
    contact: str
    request_type: str
    fields: Any


@dataclass(frozen=True)
class RequestCheck:
    verdict: Verdict
    # The validated request; also set for a hold, so an approved hold can still run.
    request: BaseModel | None = None


@dataclass(frozen=True)
class ReplyCheck:
    verdict: Verdict
    reply: BaseModel | None = None
    rules_fired: list[str] = field(default_factory=list)
    dropped_fields: list[str] = field(default_factory=list)


class Checker(Protocol):
    name: str

    async def check_request(self, ctx: CheckContext, ask: Ask) -> RequestCheck: ...

    async def check_reply(
        self, ctx: CheckContext, request_type: str, request: BaseModel, fields: Any
    ) -> ReplyCheck: ...


def parse_fields(model: type[BaseModel], fields: Any) -> BaseModel:
    """Validate fields as JSON, so strict mode accepts dates and times written as strings."""
    if not isinstance(fields, dict):
        raise TypeError("fields must be a JSON object")
    return model.model_validate_json(json.dumps(fields))


def short_errors(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors(include_url=False, include_input=False)[:5]:
        where = ".".join(str(p) for p in error["loc"]) or "fields"
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts)


HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$")
DEFAULT_WORDS = frozenset({"busy", "free"})


def free_text(value: Any, path: str = "", words: frozenset[str] = DEFAULT_WORDS) -> list[str]:
    """Paths of string values that are not HH:MM times, ISO dates or words of the type.

    This is the never-share guard: a type that crosses must have no free-text field, so no
    reason, name or code can ride along. Every type passes by construction (its output models
    hold only times, dates, booleans and codes); the guard catches a mistake in a new type.
    """
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found += free_text(item, f"{path}.{key}" if path else str(key), words)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found += free_text(item, f"{path}[{i}]", words)
    elif isinstance(value, str):
        if not (HHMM.match(value) or _is_iso_date(value) or value in words):
            found.append(path)
    return found


def _is_iso_date(value: str) -> bool:
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return len(value) == 10


class DeterministicChecker:
    """The product checker: plain code, the same answer every time (ADR 0001)."""

    name = "deterministic"

    async def check_request(self, ctx: CheckContext, ask: Ask) -> RequestCheck:
        if ctx.route is None:
            return _refuse("no_route", f"the {ctx.asker} agent may not ask {ask.contact!r}")
        if ask.request_type not in ctx.route.request_types or ask.request_type not in REQUEST_TYPES:
            known = ", ".join(ctx.route.request_types)
            return _refuse(
                "unknown_request_type",
                f"{ask.request_type!r} is not a request type on this route (known: {known})",
            )
        rtype = REQUEST_TYPES[ask.request_type]
        try:
            request = parse_fields(rtype.request_model, ask.fields)
        except TypeError as exc:
            return _refuse("invalid_fields", str(exc))
        except ValidationError as exc:
            return _refuse("invalid_fields", short_errors(exc))
        if ctx.window is not None:
            outside = _outside_window(request, ctx.window)
            if outside:
                return _refuse("outside_window", outside)
        if rtype.hold_rule:
            return _hold(rtype.hold_rule, "addresses always need you", request)
        if ctx.asks_today > ctx.route.daily_limit:
            return _hold(
                "daily_limit",
                f"daily limit reached ({ctx.route.daily_limit} asks); held for the owner",
                request,
            )
        if ctx.route.burst_max is not None and ctx.asks_in_burst > ctx.route.burst_max:
            return _hold(
                "burst",
                f"more than {ctx.route.burst_max} asks within {ctx.route.burst_minutes} "
                "minutes; held for the owner",
                request,
            )
        return RequestCheck(Verdict(Decision.ALLOW, "request_ok", "request accepted"), request)

    async def check_reply(
        self, ctx: CheckContext, request_type: str, request: BaseModel, fields: Any
    ) -> ReplyCheck:
        rtype = REQUEST_TYPES.get(request_type)
        if rtype is None or ctx.route is None or rtype.reply_model is None or rtype.trim is None:
            return ReplyCheck(Verdict(Decision.REFUSE, "unknown_request_type", "no such type"))
        try:
            reply = parse_fields(rtype.reply_model, fields)
        except TypeError as exc:
            return ReplyCheck(Verdict(Decision.REFUSE, "invalid_reply", str(exc)))
        except ValidationError as exc:
            return ReplyCheck(Verdict(Decision.REFUSE, "invalid_reply", short_errors(exc)))
        trimmed = rtype.trim(request, reply, ctx.route.detail_minutes)
        leaks = free_text(trimmed.reply.model_dump(mode="json"), words=rtype.vocabulary)
        if leaks:
            return ReplyCheck(
                Verdict(Decision.REFUSE, "never_share_free_text", f"free text in {leaks}")
            )
        return ReplyCheck(
            Verdict(Decision.ALLOW, "reply_trimmed", "reply trimmed to the allowed fields"),
            trimmed.reply,
            trimmed.rules_fired,
            trimmed.dropped_fields,
        )


def _outside_window(request: BaseModel, window: tuple[dt.date, dt.date]) -> str | None:
    lo, hi = window
    dates: list[dt.date] = []
    if isinstance(request, availability.AvailabilityRequest):
        dates = [request.start_date, request.end_date]
    elif isinstance(request, address.AddressRequest):
        dates = [request.trip_date]
    if any(d < lo or d > hi for d in dates):
        return f"dates must fall between {lo.isoformat()} and {hi.isoformat()}"
    return None


class JevChecker:
    """Phase 4 comparison arm: Jev scores each request and reply. Not built yet."""

    name = "jev"

    async def check_request(self, ctx: CheckContext, ask: Ask) -> RequestCheck:
        raise NotImplementedError("the Jev checker arrives with the Phase 4 comparison")

    async def check_reply(
        self, ctx: CheckContext, request_type: str, request: BaseModel, fields: Any
    ) -> ReplyCheck:
        raise NotImplementedError("the Jev checker arrives with the Phase 4 comparison")


class NemotronJudgeChecker:
    """Phase 4 comparison arm: Nemotron judges each request and reply. Not built yet."""

    name = "nemotron-judge"

    async def check_request(self, ctx: CheckContext, ask: Ask) -> RequestCheck:
        raise NotImplementedError("the Nemotron judge arrives with the Phase 4 comparison")

    async def check_reply(
        self, ctx: CheckContext, request_type: str, request: BaseModel, fields: Any
    ) -> ReplyCheck:
        raise NotImplementedError("the Nemotron judge arrives with the Phase 4 comparison")


class FailClosed:
    """Turns any checker error or timeout into a hold, never an allow."""

    def __init__(self, inner: Checker, timeout_s: float) -> None:
        self.inner = inner
        self.timeout_s = timeout_s
        self.name = inner.name

    async def check_request(self, ctx: CheckContext, ask: Ask) -> RequestCheck:
        try:
            return await asyncio.wait_for(self.inner.check_request(ctx, ask), self.timeout_s)
        except Exception as exc:
            return RequestCheck(_error_hold(exc))

    async def check_reply(
        self, ctx: CheckContext, request_type: str, request: BaseModel, fields: Any
    ) -> ReplyCheck:
        try:
            return await asyncio.wait_for(
                self.inner.check_reply(ctx, request_type, request, fields), self.timeout_s
            )
        except Exception as exc:
            return ReplyCheck(_error_hold(exc))


CHECKERS: Mapping[str, Callable[[], Checker]] = {
    "deterministic": DeterministicChecker,
    "jev": JevChecker,
    "nemotron-judge": NemotronJudgeChecker,
}


def make_checker(name: str, timeout_s: float) -> FailClosed:
    return FailClosed(CHECKERS[name](), timeout_s)


def _refuse(rule: str, reason: str) -> RequestCheck:
    return RequestCheck(Verdict(Decision.REFUSE, rule, reason))


def _hold(rule: str, reason: str, request: BaseModel) -> RequestCheck:
    return RequestCheck(Verdict(Decision.HOLD, rule, reason), request)


def _error_hold(exc: BaseException) -> Verdict:
    kind = "timeout" if isinstance(exc, TimeoutError) else type(exc).__name__
    return Verdict(Decision.HOLD, "checker_error", f"checker failed ({kind}); held, not shared")
