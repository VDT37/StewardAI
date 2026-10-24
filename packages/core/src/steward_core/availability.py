"""The Availability request type (ADR 0002): what may be asked, what may come back, and the
rules that trim a reply before it crosses.

Requests are strict: extra fields reject the request. Replies are accepted with extra fields so
the answering agent can add a title, a place or a note, and Steward drops them visibly (they
are recorded in the log as dropped). Only date, start, end, status and can_move cross.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

MAX_DAYS = 14
DAY_MINUTES = 24 * 60
# Which fields of a reply block may cross; everything else is dropped.
BLOCK_FIELDS = ("date", "start", "end", "status", "can_move")


class AvailabilityRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    start_date: dt.date
    end_date: dt.date

    @model_validator(mode="after")
    def _range(self) -> AvailabilityRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        if (self.end_date - self.start_date).days >= MAX_DAYS:
            raise ValueError(f"at most {MAX_DAYS} days per request")
        return self


class BlockIn(BaseModel):
    """One block as the answering agent sends it. Extra keys are kept only to be dropped."""

    model_config = ConfigDict(strict=True, extra="allow")

    date: dt.date
    start: dt.time
    end: dt.time
    status: Literal["busy", "free"]
    can_move: bool
    title: str | None = None
    location: str | None = None

    @model_validator(mode="after")
    def _order(self) -> BlockIn:
        if self.end <= self.start:
            raise ValueError("end must be after start on the same date")
        return self


class AvailabilityReplyIn(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")

    blocks: list[BlockIn]
    note: str | None = None


class Block(BaseModel):
    """One block as it crosses: no titles, places or notes. Times are HH:MM; end may be 24:00."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    date: dt.date
    start: str
    end: str
    status: Literal["busy"]
    can_move: bool


class AvailabilityReplyOut(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    blocks: list[Block]


@dataclass
class TrimResult:
    reply: AvailabilityReplyOut
    # Names of the rules that changed something, in the order they ran.
    rules_fired: list[str] = field(default_factory=list)
    # Sorted names of the fields that were present and did not cross.
    dropped_fields: list[str] = field(default_factory=list)
    widened: int = 0
    merged: int = 0
    free_dropped: int = 0
    out_of_range: int = 0


def minutes(moment: dt.time, *, round_up: bool = False) -> int:
    """Minutes since midnight; seconds round down for a start and up for an end."""
    extra = 1 if round_up and (moment.second or moment.microsecond) else 0
    return moment.hour * 60 + moment.minute + extra


def hhmm(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def widen(start: int, end: int, grid: int) -> tuple[int, int]:
    """Round start down and end up to the grid, so a block is never cut short."""
    lo = (start // grid) * grid
    hi = -(-end // grid) * grid
    return lo, min(hi, DAY_MINUTES)


def trim(request: AvailabilityRequest, reply: AvailabilityReplyIn, grid: int) -> TrimResult:
    """Apply the Availability rules: keep allowed fields, drop free and out-of-range blocks,
    widen busy blocks to the grid, and merge same-day blocks that overlap or touch and agree on
    can_move."""
    dropped: set[str] = set()
    if reply.note is not None:
        dropped.add("note")
    dropped.update(reply.model_extra or {})

    busy: list[tuple[dt.date, int, int, bool]] = []
    result = TrimResult(reply=AvailabilityReplyOut(blocks=[]))
    for block in reply.blocks:
        for name in ("title", "location"):
            if getattr(block, name) is not None:
                dropped.add(name)
        dropped.update(block.model_extra or {})
        if not request.start_date <= block.date <= request.end_date:
            result.out_of_range += 1
            continue
        if block.status == "free":
            result.free_dropped += 1
            continue
        start, end = minutes(block.start), minutes(block.end, round_up=True)
        lo, hi = widen(start, end, grid)
        if (lo, hi) != (start, end):
            result.widened += 1
        busy.append((block.date, lo, hi, block.can_move))

    merged: list[tuple[dt.date, int, int, bool]] = []
    for item in sorted(busy, key=lambda b: (b[0], b[3], b[1], b[2])):
        last = merged[-1] if merged else None
        if last and last[0] == item[0] and last[3] == item[3] and item[1] <= last[2]:
            merged[-1] = (last[0], last[1], max(last[2], item[2]), last[3])
            result.merged += 1
        else:
            merged.append(item)
    merged.sort(key=lambda b: (b[0], b[1], b[2], b[3]))

    result.reply = AvailabilityReplyOut(
        blocks=[
            Block(date=d, start=hhmm(lo), end=hhmm(hi), status="busy", can_move=move)
            for d, lo, hi, move in merged
        ]
    )
    result.dropped_fields = sorted(dropped)
    for rule, count in (
        ("fields_dropped", len(dropped)),
        ("out_of_range_dropped", result.out_of_range),
        ("free_dropped", result.free_dropped),
        ("busy_widened", result.widened),
        ("blocks_merged", result.merged),
    ):
        if count:
            result.rules_fired.append(rule)
    return result


def describe_block(block: Block) -> str:
    """'Wed 14 Oct, 14:00 to 16:00, busy, can't move'."""
    day = block.date.strftime("%a %d %b").replace(" 0", " ")
    move = "can move" if block.can_move else "can't move"
    return f"{day}, {block.start} to {block.end}, {block.status}, {move}"


def describe_reply(request: AvailabilityRequest, reply: AvailabilityReplyOut) -> str:
    span = f"{request.start_date.isoformat()} to {request.end_date.isoformat()}"
    if not reply.blocks:
        return f"No busy blocks from {span}."
    return f"Busy from {span}: " + "; ".join(describe_block(b) for b in reply.blocks) + "."


def request_message(
    request_id: str,
    asker: str,
    request: AvailabilityRequest,
    *,
    calendar_tool: str | None,
    reply_tool: str,
) -> str:
    """The message Steward sends the answering agent, from a fixed template and typed fields
    only. Nothing the asking agent wrote reaches it."""
    span = f"from {request.start_date.isoformat()} to {request.end_date.isoformat()}"
    read = (
        f"Read your calendar for those dates with the {calendar_tool} tool, then call"
        if calendar_tool
        else "Then call"
    )
    return (
        f"Steward request {request_id}: the {asker} agent asks for your availability {span}. "
        f"{read} the {reply_tool} tool with request_id {request_id} and fields "
        '{"blocks": [...]}, one block per event: date (YYYY-MM-DD), start and end (HH:MM), '
        "status (busy or free) and can_move (true or false). "
        "Only answer through the reply tool."
    )


ASK_DESCRIPTION = (
    'availability: fields {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}, at most '
    f"{MAX_DAYS} days. Returns busy blocks (date, start, end, can_move) widened to the "
    "contact's detail level, without titles, places or reasons."
)

REPLY_DESCRIPTION = (
    'availability: fields {"blocks": [{"date": "YYYY-MM-DD", "start": "HH:MM", "end": "HH:MM", '
    '"status": "busy" or "free", "can_move": true or false, "title": optional, '
    '"location": optional}], "note": optional}'
)
