import datetime as dt
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from steward_core.availability import (
    AvailabilityReplyIn,
    AvailabilityRequest,
    describe_reply,
    request_message,
    trim,
    widen,
)
from steward_core.checker import free_text, parse_fields
from steward_fixtures import planted_details

REQUEST = AvailabilityRequest(start_date=dt.date(2026, 10, 12), end_date=dt.date(2026, 10, 15))


def reply(blocks: list[dict[str, Any]], **extra: Any) -> AvailabilityReplyIn:
    parsed = parse_fields(AvailabilityReplyIn, {"blocks": blocks, **extra})
    assert isinstance(parsed, AvailabilityReplyIn)
    return parsed


def block(day: str, start: str, end: str, **extra: Any) -> dict[str, Any]:
    return {"date": day, "start": start, "end": end, "status": "busy", "can_move": False, **extra}


def test_hospital_example_from_adr_0002() -> None:
    result = trim(
        REQUEST,
        reply(
            [block("2026-10-14", "14:15", "15:45", title="Cardiology follow-up with Dr Shah")],
            note="hospital appointment, cannot be rescheduled",
        ),
        30,
    )
    assert result.reply.model_dump(mode="json") == {
        "blocks": [
            {
                "date": "2026-10-14",
                "start": "14:00",
                "end": "16:00",
                "status": "busy",
                "can_move": False,
            }
        ]
    }
    assert result.dropped_fields == ["note", "title"]
    assert result.rules_fired == ["fields_dropped", "busy_widened"]
    assert describe_reply(REQUEST, result.reply) == (
        "Busy from 2026-10-12 to 2026-10-15: Wed 14 Oct, 14:00 to 16:00, busy, can't move."
    )


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (840, 960, (840, 960)),  # already on the grid
        (855, 945, (840, 960)),  # 14:15 to 15:45
        (0, 1, (0, 30)),  # midnight start
        (1425, 1439, (1410, 1440)),  # runs to 24:00, never past it
        (841, 869, (840, 870)),
    ],
)
def test_widen_never_cuts_short(start: int, end: int, expected: tuple[int, int]) -> None:
    lo, hi = widen(start, end, 30)
    assert (lo, hi) == expected and lo <= start and hi >= end


def test_seconds_round_outward() -> None:
    result = trim(REQUEST, reply([block("2026-10-12", "14:29:30", "15:00:30")]), 30)
    (out,) = result.reply.blocks
    assert (out.start, out.end) == ("14:00", "15:30")


def test_end_of_day_crosses_as_24_00() -> None:
    result = trim(REQUEST, reply([block("2026-10-12", "23:40", "23:59")]), 30)
    assert result.reply.blocks[0].end == "24:00"


def test_merge_only_same_day_and_same_can_move() -> None:
    result = trim(
        REQUEST,
        reply(
            [
                block("2026-10-12", "08:00", "08:50"),
                block("2026-10-12", "09:00", "09:20"),  # touches after widening, merges
                block("2026-10-12", "09:10", "09:40", can_move=True),  # overlaps, other flag
                block("2026-10-13", "08:00", "08:50"),  # next day, stays apart
            ]
        ),
        30,
    )
    got = [(b.date.day, b.start, b.end, b.can_move) for b in result.reply.blocks]
    assert got == [
        (12, "08:00", "09:30", False),
        (12, "09:00", "10:00", True),
        (13, "08:00", "09:00", False),
    ]
    assert "blocks_merged" in result.rules_fired


def test_free_and_out_of_range_blocks_are_dropped() -> None:
    result = trim(
        REQUEST,
        reply(
            [
                {**block("2026-10-12", "10:00", "11:00"), "status": "free"},
                block("2026-10-20", "10:00", "11:00"),
            ]
        ),
        30,
    )
    assert result.reply.blocks == []
    assert result.rules_fired == ["out_of_range_dropped", "free_dropped"]


def test_extra_reply_keys_are_dropped_and_recorded() -> None:
    result = trim(
        REQUEST,
        reply([block("2026-10-12", "10:00", "11:00", id="evt-1", notes="x")], reason="y"),
        30,
    )
    assert result.dropped_fields == ["id", "notes", "reason"]


def test_request_is_strict() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        parse_fields(
            AvailabilityRequest,
            {"start_date": "2026-10-12", "end_date": "2026-10-15", "why": "x"},
        )
    with pytest.raises(ValidationError, match="before start_date"):
        parse_fields(AvailabilityRequest, {"start_date": "2026-10-15", "end_date": "2026-10-12"})
    with pytest.raises(ValidationError, match="at most 14 days"):
        parse_fields(AvailabilityRequest, {"start_date": "2026-10-01", "end_date": "2026-10-20"})
    with pytest.raises(ValidationError):
        parse_fields(AvailabilityRequest, {"start_date": 20261012, "end_date": "2026-10-15"})


def test_reply_types_are_strict() -> None:
    with pytest.raises(ValidationError):
        reply([{**block("2026-10-12", "10:00", "11:00"), "can_move": "false"}])
    with pytest.raises(ValidationError, match="end must be after start"):
        reply([block("2026-10-12", "11:00", "10:00")])


def test_message_names_the_tools_and_holds_no_asker_text() -> None:
    text = request_message(
        "r-0123456789ab",
        "work",
        REQUEST,
        calendar_tool="steward__calendar_list_events",
        reply_tool="steward__reply",
    )
    assert "steward__calendar_list_events" in text and "r-0123456789ab" in text
    assert "2026-10-12 to 2026-10-15" in text


def test_free_text_guard() -> None:
    clean = {"blocks": [{"date": "2026-10-12", "start": "08:00", "end": "24:00", "status": "busy"}]}
    assert free_text(clean) == []
    assert free_text({"blocks": [{"date": "2026-10-12", "why": "Dr Shah"}]}) == ["blocks[0].why"]


PLANTED = [d["text"] for d in planted_details()]
text = st.sampled_from(PLANTED) | st.text(max_size=30)
times = st.sampled_from(["07:05", "08:00", "08:50", "09:15", "12:00", "14:15", "15:45", "23:59"])


@st.composite
def planted_reply(draw: st.DrawFn) -> dict[str, Any]:
    blocks = []
    for _ in range(draw(st.integers(min_value=0, max_value=6))):
        start, end = sorted(draw(st.lists(times, min_size=2, max_size=2, unique=True)))
        blocks.append(
            {
                "date": draw(st.sampled_from(["2026-10-11", "2026-10-12", "2026-10-14"])),
                "start": start,
                "end": end,
                "status": draw(st.sampled_from(["busy", "free"])),
                "can_move": draw(st.booleans()),
                "title": draw(text),
                "location": draw(text),
                draw(st.sampled_from(["notes", "who", "reason"])): draw(text),
            }
        )
    return {"blocks": blocks, "note": draw(text), "detail": draw(text)}


@settings(max_examples=300, deadline=None)
@given(planted_reply())
def test_no_planted_detail_crosses(fields: dict[str, Any]) -> None:
    result = trim(
        REQUEST, reply(fields["blocks"], note=fields["note"], detail=fields["detail"]), 30
    )
    crossed = result.reply.model_dump_json()
    assert not [p for p in PLANTED if p.lower() in crossed.lower()]
    assert free_text(result.reply.model_dump(mode="json")) == []
