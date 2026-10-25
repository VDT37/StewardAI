"""Property test for every reply type (Phase 2 Part G, extending the Phase 1 test).

Hypothesis builds replies for travel_limits and needs with planted details placed in any field:
reasons, notes, extra keys, and the code fields themselves. Whatever it builds, either the reply
is rejected (nothing crosses) or what crosses holds no planted detail and no free text. The
availability version of this property lives with the Availability tests.
"""

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from steward_core.checker import free_text, parse_fields
from steward_core.request_types import REQUEST_TYPES
from steward_fixtures import planted_details

PLANTED = [d["text"] for d in planted_details()]
text = st.sampled_from(PLANTED) | st.text(max_size=30)
rules = st.sampled_from(["no_departures_before", "no_arrivals_after", "return_same_day"])
codes = st.sampled_from(["vegetarian", "halal", "step_free_access", "hearing_loop"])
times = st.sampled_from(["08:45", "09:00", "17:10", None])


def maybe_planted(value: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    """Usually a valid value, sometimes a planted detail in its place."""
    return value | st.sampled_from(PLANTED)


@st.composite
def limits_reply(draw: st.DrawFn) -> dict[str, Any]:
    limits = []
    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        limit: dict[str, Any] = {
            "rule": draw(maybe_planted(rules)),
            "value": draw(maybe_planted(times)),
            "applies_to": draw(maybe_planted(st.sampled_from(["weekdays", "all_days"]))),
        }
        if draw(st.booleans()):
            limit[draw(st.sampled_from(["reason", "why", "who"]))] = draw(text)
        limits.append(limit)
    return {
        "limits": limits,
        "note": draw(text),
        draw(st.sampled_from(["extra", "detail"])): draw(text),
    }


@st.composite
def needs_reply(draw: st.DrawFn) -> dict[str, Any]:
    def item() -> Any:
        if draw(st.booleans()):
            return draw(maybe_planted(codes))
        return {"need": draw(maybe_planted(codes)), "reason": draw(text), "note": draw(text)}

    return {
        "dietary": [item() for _ in range(draw(st.integers(min_value=0, max_value=3)))],
        "access": [item() for _ in range(draw(st.integers(min_value=0, max_value=3)))],
        "note": draw(text),
        "home": draw(text),
    }


def crosses(request_type: str, fields: dict[str, Any]) -> str | None:
    """What crosses for these reply fields, or None when the reply is rejected."""
    rtype = REQUEST_TYPES[request_type]
    assert rtype.reply_model is not None and rtype.trim is not None
    try:
        reply = parse_fields(rtype.reply_model, fields)
    except (ValidationError, TypeError):
        return None
    trimmed = rtype.trim(rtype.request_model(), reply, 30)
    assert free_text(trimmed.reply.model_dump(mode="json"), words=rtype.vocabulary) == []
    return trimmed.reply.model_dump_json()


@settings(max_examples=300, deadline=None)
@given(limits_reply())
def test_no_planted_detail_crosses_in_travel_limits(fields: dict[str, Any]) -> None:
    crossed = crosses("travel_limits", fields)
    if crossed is not None:
        assert not [p for p in PLANTED if p.lower() in crossed.lower()]


@settings(max_examples=300, deadline=None)
@given(needs_reply())
def test_no_planted_detail_crosses_in_needs(fields: dict[str, Any]) -> None:
    crossed = crosses("needs", fields)
    if crossed is not None:
        assert not [p for p in PLANTED if p.lower() in crossed.lower()]
