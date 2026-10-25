"""Change notices (Phase 2 Part A, decision P2-D1 option A: Steward computes them).

When Steward answers an availability request it keeps a watch on the window it answered. When
the contact's calendar changes, Steward reads the calendar for each watch, trims it with the
same Availability code, and sends the difference as typed notices: which grid slots became busy
or free. The asker learns "Thu 15 Oct, 09:00 to 10:30, now busy, can't move" and nothing about
what changed or where.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from steward_core.availability import (
    AvailabilityReplyIn,
    AvailabilityRequest,
    Block,
    hhmm,
    trim,
)


class ChangeNotice(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    date: dt.date
    start: str
    end: str
    status: Literal["busy", "free"]
    can_move: bool
    change: Literal["now_busy", "now_free"]


@dataclass
class Watch:
    """An answered availability window, and the blocks that crossed for it."""

    request_id: str
    asker: str
    contact: str
    request: AvailabilityRequest
    grid: int
    blocks: list[Block] = field(default_factory=list)


def _minutes(text: str) -> int:
    return int(text[:2]) * 60 + int(text[3:])


Slot = tuple[dt.date, bool, int]


def slots(blocks: list[Block], grid: int) -> set[Slot]:
    """Every grid slot a block covers, as (date, can_move, slot index)."""
    covered: set[Slot] = set()
    for block in blocks:
        for index in range(_minutes(block.start) // grid, _minutes(block.end) // grid):
            covered.add((block.date, block.can_move, index))
    return covered


def _runs(found: set[Slot], grid: int, change: str) -> list[ChangeNotice]:
    notices: list[ChangeNotice] = []
    ordered = sorted(found)
    i = 0
    while i < len(ordered):
        day, move, first = ordered[i]
        last = first
        while i + 1 < len(ordered) and ordered[i + 1] == (day, move, last + 1):
            i += 1
            last += 1
        notices.append(
            ChangeNotice(
                date=day,
                start=hhmm(first * grid),
                end=hhmm((last + 1) * grid),
                status="busy" if change == "now_busy" else "free",
                can_move=move,
                change="now_busy" if change == "now_busy" else "now_free",
            )
        )
        i += 1
    return notices


def diff(old: list[Block], new: list[Block], grid: int) -> list[ChangeNotice]:
    """Notices for the slots that became busy and the slots that became free."""
    before, after = slots(old, grid), slots(new, grid)
    notices = _runs(after - before, grid, "now_busy") + _runs(before - after, grid, "now_free")
    return sorted(notices, key=lambda n: (n.date, n.start, n.change))


def blocks_from_events(events: list[dict[str, Any]]) -> AvailabilityReplyIn:
    """A calendar listing ({"start": iso, "end": iso, "can_move": ...}) as an availability
    reply, so the notice path trims with exactly the code a reply goes through."""
    blocks = []
    for event in events:
        start = dt.datetime.fromisoformat(str(event["start"]))
        end = dt.datetime.fromisoformat(str(event["end"]))
        blocks.append(
            {
                "date": start.date().isoformat(),
                "start": start.strftime("%H:%M"),
                "end": "23:59" if end.date() > start.date() else end.strftime("%H:%M"),
                "status": "busy",
                "can_move": bool(event.get("can_move", False)),
            }
        )
    return AvailabilityReplyIn.model_validate_json(json.dumps({"blocks": blocks}))


def recompute(watch: Watch, events: list[dict[str, Any]]) -> list[Block]:
    return list(trim(watch.request, blocks_from_events(events), watch.grid).reply.blocks)


def describe_notice(notice: ChangeNotice) -> str:
    """'Thu 15 Oct, 09:00 to 10:30, now busy, can't move'."""
    day = notice.date.strftime("%a %d %b").replace(" 0", " ")
    move = "can move" if notice.can_move else "can't move"
    state = "now busy" if notice.change == "now_busy" else "now free"
    tail = f", {move}" if notice.change == "now_busy" else ""
    return f"{day}, {notice.start} to {notice.end}, {state}{tail}"


def notice_message(
    notice_id: str, contact: str, request_ids: list[str], notices: list[ChangeNotice]
) -> str:
    """The message the asking agent gets, from typed fields only."""
    lines = "; ".join(describe_notice(n) for n in notices)
    asked = ", ".join(request_ids)
    return (
        f"Steward change notice {notice_id} from the {contact} side, about availability you "
        f"asked for ({asked}): {lines}. Nothing else about the change is shared. If a "
        "booking now clashes, re-plan: list your bookings with travel_list_bookings and move "
        "each one that clashes with travel_change_booking (a train by option_id, a taxi by "
        "pickup_time). Changing a paid booking waits for the owner's approval; make each "
        "change once."
    )
