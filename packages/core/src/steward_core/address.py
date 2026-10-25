"""The address_for_booking request type and address stand-ins (Phase 2 Part A).

The work agent never sees the home address. It asks for one for a named booking, and Steward
always holds that ask ("addresses always need you"). If the owner taps "Taxi booking only",
Steward issues a stand-in such as [ADDR-1] bound to the agent, the booking tools, the trip date
and an expiry. It swaps in the real address only when it executes an allowed or approved call
to one of those tools for that booking. The stand-in anywhere else is refused and logged.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

STAND_IN = re.compile(r"\[ADDR-\d+\]")
SUMMARY_MAX = 200
# Upstream tools a stand-in can be used in, and the one booking call its approval covers.
ADDRESS_TOOLS = ("travel.book_taxi", "travel.change_booking")
COVERED_TOOL = "travel.book_taxi"


class AddressRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    booking_tool: Literal["travel_book_taxi"]
    trip_date: dt.date
    pickup_time: dt.time
    # Written by the work agent and shown to the owner on the card, never to another agent.
    booking_summary: str = Field(min_length=1, max_length=SUMMARY_MAX)


@dataclass
class StandIn:
    token: str
    agent: str
    hold_id: str
    tools: tuple[str, ...]
    trip_date: str
    expires: str
    # How many booking calls the owner's approval still covers (P2-D3: exactly one).
    covers: int = 1
    # Bookings made with this stand-in; later changes are only valid for these.
    booking_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "agent": self.agent,
            "hold_id": self.hold_id,
            "tools": list(self.tools),
            "trip_date": self.trip_date,
            "expires": self.expires,
            "covers": self.covers,
            "booking_ids": list(self.booking_ids),
        }


def find_stand_ins(value: Any, path: str = "") -> list[tuple[str, str]]:
    """(path, token) for every stand-in in any string inside value."""
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found += find_stand_ins(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found += find_stand_ins(item, f"{path}[{i}]")
    elif isinstance(value, str):
        found += [(path, match) for match in STAND_IN.findall(value)]
    return found


def swap_stand_ins(value: Any, real: dict[str, str]) -> Any:
    """A copy of value with each stand-in token replaced by its real value."""
    if isinstance(value, dict):
        return {key: swap_stand_ins(item, real) for key, item in value.items()}
    if isinstance(value, list):
        return [swap_stand_ins(item, real) for item in value]
    if isinstance(value, str):
        return STAND_IN.sub(lambda m: real.get(m.group(0), m.group(0)), value)
    return value


def card_text(request: AddressRequest, taxi_price: int | None) -> str:
    """The line the owner reads on the card, from typed fields plus the agent's summary."""
    day = request.trip_date.strftime("%a %d %b").replace(" 0", " ")
    price = f", £{taxi_price}" if taxi_price is not None else ""
    return (
        f"The work agent wants your home address to book a taxi on {day} "
        f"(pickup {request.pickup_time.strftime('%H:%M')}{price}). "
        f"Its note: {request.booking_summary}"
    )


ASK_DESCRIPTION = (
    'address_for_booking: fields {"booking_tool": "travel_book_taxi", "trip_date": '
    '"YYYY-MM-DD", "pickup_time": "HH:MM", "booking_summary": "<one line for the owner, at most '
    f'{SUMMARY_MAX} characters>"}}. Always held for the owner. If approved you get a stand-in '
    "such as [ADDR-1] (through hold_status) to put in the taxi booking's address; Steward fills "
    "in the real address when the booking goes out. Use the stand-in nowhere else."
)
