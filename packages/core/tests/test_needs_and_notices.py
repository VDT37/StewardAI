"""The Phase 2 request types (travel limits, needs, address) and change notices."""

import asyncio
import datetime as dt
from typing import Any

import pytest

from steward_core.address import AddressRequest, find_stand_ins, swap_stand_ins
from steward_core.availability import AvailabilityRequest, Block
from steward_core.checker import (
    Ask,
    CheckContext,
    Decision,
    DeterministicChecker,
    free_text,
    parse_fields,
)
from steward_core.needs import (
    NEED_WORDS,
    NeedsReplyIn,
    NeedsRequest,
    TravelLimitsReplyIn,
    TravelLimitsRequest,
    describe_limits,
    describe_needs,
    trim_limits,
    trim_needs,
)
from steward_core.notices import Watch, describe_notice, diff, recompute
from steward_core.policy import ContactRule

ROUTE = ContactRule.model_validate(
    {
        "from": "work",
        "to": "personal",
        "request_types": ["availability", "travel_limits", "needs", "address_for_booking"],
    }
)
WINDOW = (dt.date(2026, 10, 12), dt.date(2026, 11, 8))


def ctx() -> CheckContext:
    return CheckContext("work", "personal", ROUTE, 1, WINDOW)


def test_travel_limits_cross_as_codes_without_the_reason() -> None:
    fields = {
        "limits": [
            {
                "rule": "no_departures_before",
                "value": "08:45",
                "applies_to": "weekdays",
                "reason": "School run with Maya every weekday morning",
            }
        ],
        "note": "it is the school run",
    }
    reply = parse_fields(TravelLimitsReplyIn, fields)
    trimmed = trim_limits(TravelLimitsRequest(), reply, 30)
    out = trimmed.reply.model_dump(mode="json")
    assert out == {
        "limits": [{"rule": "no_departures_before", "value": "09:00", "applies_to": "weekdays"}]
    }
    assert trimmed.dropped_fields == ["note", "reason"]
    assert "time_rounded_stricter" in trimmed.rules_fired
    assert describe_limits(TravelLimitsRequest(), trimmed.reply) == (
        "Travel limits: no departures before 9am on weekdays."
    )
    assert "Maya" not in str(out) and "school" not in str(out)


def test_arrival_limits_round_earlier_and_bad_rules_are_rejected() -> None:
    reply = parse_fields(
        TravelLimitsReplyIn,
        {"limits": [{"rule": "no_arrivals_after", "value": "21:10", "applies_to": "all_days"}]},
    )
    out = trim_limits(TravelLimitsRequest(), reply, 30).reply.model_dump(mode="json")
    assert out["limits"][0]["value"] == "21:00"
    with pytest.raises(ValueError):
        parse_fields(TravelLimitsReplyIn, {"limits": [{"rule": "never_before_lunch"}]})
    with pytest.raises(ValueError):
        parse_fields(TravelLimitsReplyIn, {"limits": [{"rule": "no_departures_before"}]})


def test_needs_cross_as_codes_and_reasons_are_dropped() -> None:
    reply = parse_fields(
        NeedsReplyIn,
        {
            "dietary": [{"need": "vegetarian", "reason": "Religious reasons"}],
            "access": ["step_free_access", {"need": "step_free_access", "reason": "knee surgery"}],
        },
    )
    trimmed = trim_needs(NeedsRequest(), reply, 30)
    assert trimmed.reply.model_dump(mode="json") == {
        "dietary": ["vegetarian"],
        "access": ["step_free_access"],
    }
    assert trimmed.dropped_fields == ["reason"]
    assert describe_needs(NeedsRequest(), trimmed.reply) == (
        "Dietary needs: vegetarian. Access needs: step free access."
    )
    assert free_text(trimmed.reply.model_dump(mode="json"), words=NEED_WORDS) == []
    with pytest.raises(ValueError):
        parse_fields(NeedsReplyIn, {"dietary": ["no pork please"]})


def test_address_requests_are_always_held() -> None:
    fields = {
        "booking_tool": "travel_book_taxi",
        "trip_date": "2026-10-15",
        "pickup_time": "09:00",
        "booking_summary": "Taxi to Exeter St Davids for the 09:30 train",
    }
    result = asyncio.run(
        DeterministicChecker().check_request(ctx(), Ask("personal", "address_for_booking", fields))
    )
    assert result.verdict.decision is Decision.HOLD
    assert result.verdict.rule == "address_needs_owner"
    assert isinstance(result.request, AddressRequest)
    long = {**fields, "booking_summary": "x" * 201}
    refused = asyncio.run(
        DeterministicChecker().check_request(ctx(), Ask("personal", "address_for_booking", long))
    )
    assert refused.verdict.rule == "invalid_fields"


def test_stand_ins_are_found_and_swapped_anywhere() -> None:
    args: dict[str, Any] = {"address": "[ADDR-1]", "notes": ["pick up at [ADDR-1]"]}
    assert find_stand_ins(args) == [("address", "[ADDR-1]"), ("notes[0]", "[ADDR-1]")]
    swapped = swap_stand_ins(args, {"[ADDR-1]": "27 Ashdown Road"})
    assert swapped == {"address": "27 Ashdown Road", "notes": ["pick up at 27 Ashdown Road"]}
    assert args["address"] == "[ADDR-1]"


def block(day: str, start: str, end: str, move: bool = False) -> Block:
    return Block(
        date=dt.date.fromisoformat(day), start=start, end=end, status="busy", can_move=move
    )


def test_a_new_appointment_becomes_a_now_busy_notice() -> None:
    old = [block("2026-10-15", "08:00", "09:00")]
    new = [block("2026-10-15", "08:00", "10:30")]
    (notice,) = diff(old, new, 30)
    assert notice.model_dump(mode="json") == {
        "date": "2026-10-15",
        "start": "09:00",
        "end": "10:30",
        "status": "busy",
        "can_move": False,
        "change": "now_busy",
    }
    assert describe_notice(notice) == "Thu 15 Oct, 09:00 to 10:30, now busy, can't move"
    (freed,) = diff(new, old, 30)
    assert freed.change == "now_free" and describe_notice(freed).endswith("now free")


def test_recompute_trims_calendar_events_like_a_reply() -> None:
    request = AvailabilityRequest(start_date=dt.date(2026, 10, 15), end_date=dt.date(2026, 10, 15))
    watch = Watch("r-1", "work", "personal", request, 30, [])
    events = [
        {
            "start": "2026-10-15T09:00:00+01:00",
            "end": "2026-10-15T10:30:00+01:00",
            "can_move": False,
            "title": "Fasting blood test, GP surgery",
        },
        {
            "start": "2026-10-15T08:00:00+01:00",
            "end": "2026-10-15T08:50:00+01:00",
            "can_move": False,
        },
    ]
    blocks = recompute(watch, events)
    assert [(b.start, b.end) for b in blocks] == [("08:00", "10:30")]
    assert "blood" not in str([b.model_dump() for b in blocks])
