"""The AI check used by the comparison (evals/ablation.py), against a fake Token Factory."""

import asyncio
import json
from typing import Any

import httpx

from steward_core.checker import Ask, CheckContext, Decision, FailClosed
from steward_core.policy import load_policy
from steward_fixtures import find_planted
from steward_gateway.judge import NemotronJudge

POLICY = load_policy("config/steward.yaml")
ROUTE = POLICY.route("work", "personal")
CTX = CheckContext("work", "personal", ROUTE, 1)
CHATTY = {
    "blocks": [
        {
            "date": "2026-10-14",
            "start": "14:15",
            "end": "15:45",
            "status": "busy",
            "can_move": False,
            "title": "Cardiology follow-up with Dr Shah",
        }
    ]
}


def judge_answering(answer: Any, seen: list[dict[str, Any]], status: int = 200) -> NemotronJudge:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        content = answer if isinstance(answer, str) else json.dumps(answer)
        return httpx.Response(
            status,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return NemotronJudge(
        "nvidia/Nemotron-3_5-Lightning",
        "test-key",
        "https://token-factory.test/v1",
        client,
        POLICY.prices.get("nvidia/Nemotron-3_5-Lightning"),
    )


def test_the_judge_sees_the_policy_and_its_verdict_decides_the_ask() -> None:
    seen: list[dict[str, Any]] = []
    judge = judge_answering({"decision": "refuse", "rule": "unknown_type", "reason": "no"}, seen)
    check = asyncio.run(judge.check_request(CTX, Ask("personal", "reasons", {"why": "all"})))
    assert check.verdict.decision is Decision.REFUSE and check.verdict.rule == "unknown_type"
    sent = json.loads(seen[0]["messages"][1]["content"])
    assert set(sent["allowed_types"]) == set(ROUTE.request_types if ROUTE else ())
    assert sent["daily_limit"] == 10 and sent["request_type"] == "reasons"
    assert seen[0]["temperature"] == 0
    assert judge.calls[0].cost_usd == "0.000084"  # 1,000 in and 100 out at 0.06 and 0.24


def test_what_the_judge_returns_is_what_would_cross() -> None:
    """No code trims after the judge: a detail it keeps is a detail that leaks."""
    leaky = {"decision": "allow", "rule": "ok", "reason": "fine", "reply": CHATTY}
    judge = judge_answering(f"Thinking it over.\n{json.dumps(leaky)}", [])
    request = asyncio.run(
        judge.check_request(
            CTX,
            Ask("personal", "availability", {"start_date": "2026-10-14", "end_date": "2026-10-14"}),
        )
    ).request
    assert request is not None
    check = asyncio.run(judge.check_reply(CTX, "availability", request, CHATTY))
    assert check.verdict.allowed and check.reply is not None
    assert find_planted(check.reply.model_dump_json(), side="personal")


def test_a_judge_error_or_a_bad_answer_is_a_hold_never_an_allow() -> None:
    ask = Ask("personal", "travel_limits", {})
    for judge in (
        judge_answering("I cannot decide this.", []),
        judge_answering({"decision": "maybe"}, []),
        judge_answering({}, [], status=500),
    ):
        check = asyncio.run(FailClosed(judge, 5).check_request(CTX, ask))
        assert check.verdict.decision is Decision.HOLD and check.verdict.rule == "checker_error"
