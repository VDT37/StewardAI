import asyncio
import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from steward_core.availability import AvailabilityRequest
from steward_core.checker import (
    Ask,
    CheckContext,
    Decision,
    DeterministicChecker,
    FailClosed,
    ReplyCheck,
    RequestCheck,
    make_checker,
)
from steward_core.metering import call_cost, mask_body
from steward_core.policy import ContactRule, Price, exposed_name, load_policy

ROOT = Path(__file__).resolve().parents[3]
ROUTE = ContactRule.model_validate(
    {"from": "work", "to": "personal", "request_types": ["availability"], "daily_limit": 10}
)
FIELDS = {"start_date": "2026-10-12", "end_date": "2026-10-15"}
WINDOW = (dt.date(2026, 10, 12), dt.date(2026, 11, 8))


def ctx(asks_today: int = 1, route: ContactRule | None = ROUTE) -> CheckContext:
    return CheckContext("work", "personal", route, asks_today, WINDOW)


def check(ask: Ask, context: CheckContext | None = None) -> RequestCheck:
    return asyncio.run(DeterministicChecker().check_request(context or ctx(), ask))


def test_valid_ask_is_allowed() -> None:
    result = check(Ask("personal", "availability", FIELDS))
    assert result.verdict.decision is Decision.ALLOW
    assert isinstance(result.request, AvailabilityRequest)


@pytest.mark.parametrize(
    ("ask", "context", "rule"),
    [
        (Ask("personal", "reasons", {"why": "each day"}), None, "unknown_request_type"),
        (Ask("bank", "availability", FIELDS), ctx(route=None), "no_route"),
        (Ask("personal", "availability", FIELDS), ctx(asks_today=11), "daily_limit"),
        (Ask("personal", "availability", {**FIELDS, "why": "x"}), None, "invalid_fields"),
        (Ask("personal", "availability", "12 to 15 Oct"), None, "invalid_fields"),
        (
            Ask("personal", "availability", {"start_date": "2026-12-01", "end_date": "2026-12-02"}),
            None,
            "outside_window",
        ),
    ],
)
def test_refusals(ask: Ask, context: CheckContext | None, rule: str) -> None:
    result = check(ask, context)
    assert result.verdict.decision is Decision.REFUSE and result.verdict.rule == rule


def test_tenth_ask_passes_and_eleventh_is_refused() -> None:
    assert check(Ask("personal", "availability", FIELDS), ctx(10)).verdict.allowed
    assert check(Ask("personal", "availability", FIELDS), ctx(11)).verdict.rule == "daily_limit"


def test_reply_is_trimmed() -> None:
    request = AvailabilityRequest(start_date=dt.date(2026, 10, 12), end_date=dt.date(2026, 10, 15))
    fields = {
        "blocks": [
            {
                "date": "2026-10-14",
                "start": "14:15",
                "end": "15:45",
                "status": "busy",
                "can_move": False,
                "location": "Exeter Riverside Hospital",
            }
        ],
        "note": "hospital",
    }
    result = asyncio.run(DeterministicChecker().check_reply(ctx(), "availability", request, fields))
    assert result.verdict.allowed and result.reply is not None
    assert "Riverside" not in result.reply.model_dump_json()
    assert result.dropped_fields == ["location", "note"]
    bad = asyncio.run(
        DeterministicChecker().check_reply(ctx(), "availability", request, {"blocks": "busy"})
    )
    assert bad.verdict.rule == "invalid_reply"


class Broken:
    name = "broken"

    def __init__(self, error: BaseException | None = None, delay: float = 0) -> None:
        self.error = error
        self.delay = delay

    async def check_request(self, ctx: CheckContext, ask: Ask) -> RequestCheck:
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        raise AssertionError("unreachable")

    async def check_reply(
        self, ctx: CheckContext, request_type: str, request: BaseModel, fields: Any
    ) -> ReplyCheck:
        raise ValueError("boom")


def test_fail_closed_turns_errors_and_timeouts_into_holds() -> None:
    ask = Ask("personal", "availability", FIELDS)
    error = asyncio.run(FailClosed(Broken(RuntimeError("x")), 1).check_request(ctx(), ask))
    slow = asyncio.run(FailClosed(Broken(delay=1), 0.01).check_request(ctx(), ask))
    reply = asyncio.run(FailClosed(Broken(), 1).check_reply(ctx(), "availability", None, {}))  # type: ignore[arg-type]
    for result in (error, slow, reply):
        assert result.verdict.decision is Decision.HOLD
        assert result.verdict.rule == "checker_error"
    assert "timeout" in slow.verdict.reason


def test_stub_checkers_hold() -> None:
    result = asyncio.run(make_checker("jev", 1).check_request(ctx(), Ask("personal", "x", {})))
    assert result.verdict.decision is Decision.HOLD


def test_cost_is_a_decimal_string() -> None:
    price = Price(input_per_million=Decimal("0.30"), output_per_million=Decimal("0.90"))
    usage = {"prompt_tokens": 13000, "completion_tokens": 500}
    assert call_cost(usage, price) == "0.004350"
    assert call_cost(None, price) is None
    assert call_cost({"prompt_tokens": 1}, price) is None


def test_mask_replaces_and_reports_numbers_only() -> None:
    body = {
        "messages": [
            {"role": "user", "content": "card 4111 for Dr Shah"},
            {"role": "user", "content": [{"type": "text", "text": "4111 again"}]},
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "f", "arguments": '{"n": "4111"}'}}],
            },
        ]
    }
    masked, fired = mask_body(body, ["nothing", "4111"])
    assert fired == [2]
    assert "4111" not in str(masked) and "[MASKED-2]" in masked["messages"][0]["content"]
    assert body["messages"][0]["content"] == "card 4111 for Dr Shah"  # original untouched


def test_policy_file_loads() -> None:
    policy = load_policy(ROOT / "config" / "steward.yaml")
    assert set(policy.agents) == {"work", "personal"}
    route = policy.route("work", "personal")
    assert route is not None and route.request_types == ("availability",)
    assert policy.route("personal", "work") is None
    assert [exposed_name(t) for t in policy.agents["personal"].tools] == [
        "calendar_list_events",
        "gmail_list_messages",
        "gmail_read_message",
        "todo_list_items",
    ]


def test_policy_rejects_clashes_and_unknown_agents() -> None:
    base: dict[str, Any] = {
        "agents": {
            "work": {
                "pass_env": "A",
                "model": "m",
                "budget_usd_per_day": "1",
                "tools": ["work_calendar.list_events", "personal_calendar.list_events"],
            }
        },
        "prices": {"m": {"input_per_million": "1", "output_per_million": "1"}},
    }
    with pytest.raises(ValidationError, match="same name"):
        load_policy_dict(base)
    base["agents"]["work"]["tools"] = []
    base["contacts"] = [{"from": "work", "to": "ghost", "request_types": ["availability"]}]
    with pytest.raises(ValidationError, match="unknown agents"):
        load_policy_dict(base)


def load_policy_dict(data: dict[str, Any]) -> None:
    from steward_core.policy import Policy

    Policy.model_validate(data)
