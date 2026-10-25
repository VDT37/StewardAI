"""The travel_limits and needs request types (Phase 2 Part A).

Both are answered from the personal agent's needs profile. Every part of what crosses comes from
a fixed set of codes, so no reason can ride along: the fixture's reasons ("School run with Maya",
"Religious reasons", "Recovering from knee surgery") are accepted in the reply and dropped
visibly, like titles and notes in an availability reply. Some dietary needs relate to health,
so the type carries the need and never the reason.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, model_validator

from steward_core.trimmed import Trimmed

LimitRule = Literal[
    "no_departures_before",
    "no_arrivals_after",
    "return_same_day",
    "no_overnight_stays",
    "no_flights",
]
AppliesTo = Literal["weekdays", "weekends", "all_days"]
Dietary = Literal["vegetarian", "vegan", "halal", "kosher", "gluten_free", "nut_free"]
Access = Literal["step_free_access", "wheelchair_access", "hearing_loop", "seated_rest"]

# Rules with a time, and which way the time moves to reach the grid: always the stricter way,
# so a limit is never loosened (no departures before 08:45 crosses as 09:00).
TIMED_RULES: dict[str, str] = {"no_departures_before": "up", "no_arrivals_after": "down"}

LIMIT_WORDS = frozenset(get_args(LimitRule)) | frozenset(get_args(AppliesTo))
NEED_WORDS = frozenset(get_args(Dietary)) | frozenset(get_args(Access))


class TravelLimitsRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class LimitIn(BaseModel):
    """One limit as the answering agent sends it; reason and extra keys are only dropped."""

    model_config = ConfigDict(strict=True, extra="allow")

    rule: LimitRule
    value: dt.time | None = None
    applies_to: AppliesTo = "all_days"
    reason: str | None = None

    @model_validator(mode="after")
    def _value(self) -> LimitIn:
        if self.rule in TIMED_RULES and self.value is None:
            raise ValueError(f"{self.rule} needs a value (HH:MM)")
        return self


class TravelLimitsReplyIn(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")

    limits: list[LimitIn]
    note: str | None = None


class Limit(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    rule: LimitRule
    value: str | None
    applies_to: AppliesTo


class TravelLimitsReplyOut(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    limits: list[Limit]


class NeedsRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class DietaryItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")

    need: Dietary
    reason: str | None = None


class AccessItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")

    need: Access
    reason: str | None = None


class NeedsReplyIn(BaseModel):
    """Each need may be a bare code or {"need": code, "reason": ...}."""

    model_config = ConfigDict(strict=True, extra="allow")

    dietary: list[Dietary | DietaryItem] = []
    access: list[Access | AccessItem] = []
    note: str | None = None


class NeedsReplyOut(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    dietary: list[Dietary]
    access: list[Access]


def _grid_time(value: dt.time, grid: int, direction: str) -> str:
    """Move a time onto the grid in the stricter direction; "up" may reach 24:00."""
    total = value.hour * 60 + value.minute
    if direction == "up":
        total += 1 if value.second or value.microsecond else 0
        total = min(-(-total // grid) * grid, 24 * 60)
    else:
        total = total // grid * grid
    return f"{total // 60:02d}:{total % 60:02d}"


def trim_limits(request: BaseModel, reply: BaseModel, grid: int) -> Trimmed:
    assert isinstance(reply, TravelLimitsReplyIn)
    dropped: set[str] = set(reply.model_extra or {})
    if reply.note is not None:
        dropped.add("note")
    rounded = 0
    seen: dict[tuple[str, str | None, str], Limit] = {}
    for item in reply.limits:
        if item.reason is not None:
            dropped.add("reason")
        dropped.update(item.model_extra or {})
        value: str | None = None
        if item.rule in TIMED_RULES:
            assert item.value is not None
            value = _grid_time(item.value, grid, TIMED_RULES[item.rule])
            if value != item.value.strftime("%H:%M") or item.value.second:
                rounded += 1
        elif item.value is not None:
            dropped.add("value")
        limit = Limit(rule=item.rule, value=value, applies_to=item.applies_to)
        seen[(limit.rule, limit.value, limit.applies_to)] = limit
    limits = sorted(seen.values(), key=lambda lim: (lim.rule, lim.applies_to, lim.value or ""))
    result = Trimmed(TravelLimitsReplyOut(limits=limits), dropped_fields=sorted(dropped))
    for rule, count in (
        ("fields_dropped", len(dropped)),
        ("time_rounded_stricter", rounded),
        ("duplicates_merged", len(reply.limits) - len(limits)),
    ):
        if count:
            result.rules_fired.append(rule)
    return result


def trim_needs(request: BaseModel, reply: BaseModel, grid: int) -> Trimmed:
    assert isinstance(reply, NeedsReplyIn)
    dropped: set[str] = set(reply.model_extra or {})
    if reply.note is not None:
        dropped.add("note")
    dietary: set[str] = set()
    access: set[str] = set()
    for diet in reply.dietary:
        if isinstance(diet, DietaryItem):
            if diet.reason is not None:
                dropped.add("reason")
            dropped.update(diet.model_extra or {})
            dietary.add(diet.need)
        else:
            dietary.add(diet)
    for need in reply.access:
        if isinstance(need, AccessItem):
            if need.reason is not None:
                dropped.add("reason")
            dropped.update(need.model_extra or {})
            access.add(need.need)
        else:
            access.add(need)
    out = NeedsReplyOut.model_validate({"dietary": sorted(dietary), "access": sorted(access)})
    total_in = len(reply.dietary) + len(reply.access)
    result = Trimmed(out, dropped_fields=sorted(dropped))
    for rule, count in (
        ("fields_dropped", len(dropped)),
        ("duplicates_merged", total_in - len(dietary) - len(access)),
    ):
        if count:
            result.rules_fired.append(rule)
    return result


def _words(code: str) -> str:
    return code.replace("_", " ")


def _clock(hhmm: str) -> str:
    """'09:00' -> '9am', '13:30' -> '1:30pm'."""
    hour, minute = int(hhmm[:2]), int(hhmm[3:])
    suffix = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    return f"{hour12}{suffix}" if minute == 0 else f"{hour12}:{minute:02d}{suffix}"


def describe_limit(limit: Limit) -> str:
    """'no departures before 9am on weekdays'."""
    days = {"weekdays": "on weekdays", "weekends": "at weekends", "all_days": "on any day"}
    text = _words(limit.rule)
    if limit.value is not None:
        text += f" {_clock(limit.value)}"
    return f"{text} {days[limit.applies_to]}"


def describe_limits(request: BaseModel, reply: BaseModel) -> str:
    assert isinstance(reply, TravelLimitsReplyOut)
    if not reply.limits:
        return "No travel limits."
    return "Travel limits: " + "; ".join(describe_limit(lim) for lim in reply.limits) + "."


def describe_needs(request: BaseModel, reply: BaseModel) -> str:
    assert isinstance(reply, NeedsReplyOut)
    dietary = ", ".join(_words(d) for d in reply.dietary) or "none"
    access = ", ".join(_words(a) for a in reply.access) or "none"
    return f"Dietary needs: {dietary}. Access needs: {access}."


def limits_message(
    request_id: str, asker: str, *, profile_tool: str | None, reply_tool: str
) -> str:
    read = (
        f"Read your needs profile with the {profile_tool} tool, then call"
        if profile_tool
        else "Then call"
    )
    rules = ", ".join(get_args(LimitRule))
    return (
        f"Steward request {request_id}: the {asker} agent asks for your travel limits. "
        f"{read} the {reply_tool} tool with request_id {request_id} and fields "
        '{"limits": [...]}, one entry per limit: rule (one of ' + rules + "), value (HH:MM, "
        "or null for rules without a time) and applies_to (weekdays, weekends or all_days). "
        "Only answer through the reply tool."
    )


def needs_message(request_id: str, asker: str, *, profile_tool: str | None, reply_tool: str) -> str:
    read = (
        f"Read your needs profile with the {profile_tool} tool, then call"
        if profile_tool
        else "Then call"
    )
    return (
        f"Steward request {request_id}: the {asker} agent asks for your dietary and access "
        f"needs. {read} the {reply_tool} tool with request_id {request_id} and fields "
        '{"dietary": [...], "access": [...]} using only these codes. Dietary: '
        + ", ".join(get_args(Dietary))
        + ". Access: "
        + ", ".join(get_args(Access))
        + ". Only answer through the reply tool."
    )


LIMITS_ASK = (
    "travel_limits: fields {}. Returns the owner's travel limits as codes "
    '(for example {"rule": "no_departures_before", "value": "09:00", "applies_to": '
    '"weekdays"}), without reasons.'
)
NEEDS_ASK = (
    "needs: fields {}. Returns dietary and access needs as codes (for example vegetarian, "
    "step_free_access), without reasons."
)
LIMITS_REPLY = (
    'travel_limits: fields {"limits": [{"rule": "...", "value": "HH:MM" or null, '
    '"applies_to": "weekdays" | "weekends" | "all_days", "reason": optional}]}'
)
NEEDS_REPLY = (
    'needs: fields {"dietary": [codes], "access": [codes]}; a code may also be '
    '{"need": code, "reason": optional}'
)
