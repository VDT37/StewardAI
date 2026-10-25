"""The request types one agent may ask another (ADR 0002), in one registry.

Each type has a strict request model, a reply model that accepts extra keys only to drop them,
the trim function that decides what crosses, the words the never-share guard allows in what
crosses, and the texts Steward writes: the ask tool's description, the reply shape, the message
to the answering agent and the summary for the asker. address_for_booking has no reply: the
owner decides it and Steward answers with a stand-in.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import BaseModel

from steward_core import address, availability, needs
from steward_core.trimmed import Trimmed


@dataclass(frozen=True)
class RequestType:
    name: str
    request_model: type[BaseModel]
    reply_model: type[BaseModel] | None
    trim: Callable[[BaseModel, BaseModel, int], Trimmed] | None
    describe: Callable[[BaseModel, BaseModel], str] | None
    # Words allowed in what crosses, besides HH:MM times and ISO dates.
    vocabulary: frozenset[str]
    ask_description: str
    reply_description: str | None
    # The contact's tool (exposed name) the request message tells it to read first.
    read_tool: str | None = None
    # Every ask of this type is held for the owner under this rule.
    hold_rule: str | None = None


def _trim_availability(request: BaseModel, reply: BaseModel, grid: int) -> Trimmed:
    assert isinstance(request, availability.AvailabilityRequest)
    assert isinstance(reply, availability.AvailabilityReplyIn)
    result = availability.trim(request, reply, grid)
    return Trimmed(result.reply, result.rules_fired, result.dropped_fields)


def _describe_availability(request: BaseModel, reply: BaseModel) -> str:
    assert isinstance(request, availability.AvailabilityRequest)
    assert isinstance(reply, availability.AvailabilityReplyOut)
    return availability.describe_reply(request, reply)


REQUEST_TYPES: Mapping[str, RequestType] = {
    "availability": RequestType(
        name="availability",
        request_model=availability.AvailabilityRequest,
        reply_model=availability.AvailabilityReplyIn,
        trim=_trim_availability,
        describe=_describe_availability,
        vocabulary=frozenset({"busy", "free"}),
        ask_description=availability.ASK_DESCRIPTION,
        reply_description=availability.REPLY_DESCRIPTION,
        read_tool="calendar_list_events",
    ),
    "travel_limits": RequestType(
        name="travel_limits",
        request_model=needs.TravelLimitsRequest,
        reply_model=needs.TravelLimitsReplyIn,
        trim=needs.trim_limits,
        describe=needs.describe_limits,
        vocabulary=needs.LIMIT_WORDS,
        ask_description=needs.LIMITS_ASK,
        reply_description=needs.LIMITS_REPLY,
        read_tool="profile_read_needs",
    ),
    "needs": RequestType(
        name="needs",
        request_model=needs.NeedsRequest,
        reply_model=needs.NeedsReplyIn,
        trim=needs.trim_needs,
        describe=needs.describe_needs,
        vocabulary=needs.NEED_WORDS,
        ask_description=needs.NEEDS_ASK,
        reply_description=needs.NEEDS_REPLY,
        read_tool="profile_read_needs",
    ),
    "address_for_booking": RequestType(
        name="address_for_booking",
        request_model=address.AddressRequest,
        reply_model=None,
        trim=None,
        describe=None,
        vocabulary=frozenset(),
        ask_description=address.ASK_DESCRIPTION,
        reply_description=None,
        hold_rule="address_needs_owner",
    ),
}


def request_message(
    rtype: RequestType,
    request_id: str,
    asker: str,
    request: BaseModel,
    *,
    read_tool: str | None,
    reply_tool: str,
) -> str:
    """The message Steward sends the answering agent, from a fixed template and typed fields
    only. Nothing the asking agent wrote reaches it."""
    if rtype.name == "availability":
        assert isinstance(request, availability.AvailabilityRequest)
        return availability.request_message(
            request_id, asker, request, calendar_tool=read_tool, reply_tool=reply_tool
        )
    if rtype.name == "travel_limits":
        return needs.limits_message(
            request_id, asker, profile_tool=read_tool, reply_tool=reply_tool
        )
    if rtype.name == "needs":
        return needs.needs_message(request_id, asker, profile_tool=read_tool, reply_tool=reply_tool)
    raise ValueError(f"{rtype.name} is not delivered to an agent")
