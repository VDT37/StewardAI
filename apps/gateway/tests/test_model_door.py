import json
from pathlib import Path

import pytest
from gateway_helpers import (
    KEY,
    OWNER,
    PERSONAL,
    WORK,
    Upstream,
    bearer,
    fast_policy,
    http_app,
    make_settings,
)

from steward_core.store import Event
from steward_gateway.model_door import StreamScanner
from steward_gateway.settings import PassBook, Settings

STREAM_BODY = (
    b'data: {"choices":[{"index":0,"delta":{"content":"1, 2"}}]}\n\n'
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c1",'
    b'"function":{"name":"ask","arguments":"{\\"start"}}]}}]}\n\n'
    b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":7,"total_tokens":12}}\n\n'
    b"data: [DONE]\n\n"
)


def model_calls(events: list[Event]) -> list[Event]:
    return [e for e in events if e.kind == "model.call"]


def test_settings_reject_missing_and_reused_tokens(tmp_path: Path) -> None:
    env = {
        "NEBIUS_API_KEY": KEY,
        "STEWARD_OWNER_TOKEN": OWNER,
        "STEWARD_PASS_WORK": WORK,
        "STEWARD_PASS_PERSONAL": PERSONAL,
        "STEWARD_POLICY": "config/steward.yaml",
    }
    with pytest.raises(RuntimeError, match="NEBIUS_API_KEY"):
        Settings.from_env({**env, "NEBIUS_API_KEY": " "})
    with pytest.raises(RuntimeError, match="must all differ"):
        Settings.from_env({**env, "STEWARD_PASS_PERSONAL": WORK})
    with pytest.raises(RuntimeError, match="must all differ"):
        Settings.from_env({**env, "STEWARD_OWNER_TOKEN": PERSONAL})


def test_pass_book_maps_passes() -> None:
    book = PassBook({"work": WORK, "personal": PERSONAL}, OWNER)
    assert book.identify(f"Bearer {WORK}") == "work"
    assert book.identify(f"bearer {PERSONAL}") == "personal"
    assert book.identify("Bearer wrong") is None and book.identify(WORK) is None
    assert book.is_owner(f"Bearer {OWNER}") and not book.is_owner(f"Bearer {WORK}")


def test_models_lists_only_the_pass_model(tmp_path: Path) -> None:
    with http_app(tmp_path) as (client, _, ctx):
        assert client.get("/v1/models").status_code == 401
        response = client.get("/v1/models", headers=bearer(WORK))
        assert [m["id"] for m in response.json()["data"]] == ["nvidia/nemotron-3-super-120b-a12b"]
        denied = ctx.store.events(kind="auth.denied")
        assert len(denied) == 1 and denied[0].payload["status"] == 401


def test_chat_forwards_meters_and_logs(tmp_path: Path) -> None:
    with http_app(tmp_path) as (client, upstream, ctx):
        response = client.post(
            "/v1/chat/completions",
            headers={**bearer(PERSONAL), "Accept-Encoding": "gzip"},
            json={"model": "anything", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 200
        assert response.headers["x-ratelimit-remaining-tokens"] == "399000"
        sent = upstream.requests[0]
        assert str(sent.url) == "https://api.tokenfactory.nebius.com/v1/chat/completions"
        assert sent.headers["authorization"] == f"Bearer {KEY}"
        assert sent.headers["accept-encoding"] == "identity"
        assert json.loads(sent.content)["model"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
        (event,) = model_calls(ctx.store.events())
        assert event.agent == "personal" and event.door == "model"
        # Nano: 13000 * 0.06 + 500 * 0.24 per million = 0.000900 USD
        assert event.payload["cost_usd"] == "0.000900"
        assert event.payload["usage"]["total_tokens"] == 13500
        assert event.payload["tool_calls"] == ["calendar_list_events"]
        summary = client.get("/admin/agents", headers=bearer(OWNER)).json()
        spend = {a["id"]: a["spend_today_usd"] for a in summary}
        assert spend == {"work": "0", "personal": "0.000900"}


def test_stream_is_relayed_and_usage_forced(tmp_path: Path) -> None:
    with http_app(tmp_path, Upstream(STREAM_BODY)) as (client, upstream, ctx):
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers=bearer(WORK),
            json={"messages": [], "stream": True, "stream_options": {"other": 1}},
        ) as response:
            assert response.status_code == 200
            relayed = b"".join(response.iter_raw())
        assert relayed == STREAM_BODY
        sent = json.loads(upstream.requests[0].content)
        assert sent["stream_options"] == {"other": 1, "include_usage": True}
        (event,) = model_calls(ctx.store.events())
        assert event.payload["stream"] is True and event.payload["usage"]["total_tokens"] == 12
        assert event.payload["tool_calls"] == ["ask"]
        assert event.payload["cost_usd"] == "0.000008"


def test_mask_applies_before_the_call_leaves(tmp_path: Path) -> None:
    policy = fast_policy().model_copy(update={"mask": ("4111 1111", "Dr Shah")})
    with http_app(tmp_path, policy=policy) as (client, upstream, ctx):
        client.post(
            "/v1/chat/completions",
            headers=bearer(WORK),
            json={"messages": [{"role": "user", "content": "Dr Shah, card 4111 1111"}]},
        )
        sent = json.loads(upstream.requests[0].content)
        assert sent["messages"][0]["content"] == "[MASKED-2], card [MASKED-1]"
        (event,) = model_calls(ctx.store.events())
        assert event.payload["mask_fired"] == [1, 2]
        assert "Shah" not in json.dumps(event.payload)


def test_bad_pass_bad_body_and_suspended_pass(tmp_path: Path) -> None:
    with http_app(tmp_path) as (client, upstream, ctx):
        assert client.post("/v1/chat/completions", json={}).status_code == 401
        bad = client.post("/v1/chat/completions", headers=bearer(WORK), content=b"not json")
        assert bad.status_code == 400
        assert client.post("/admin/agents/work/suspend", headers=bearer(WORK)).status_code == 401
        assert client.post("/admin/agents/work/suspend", headers=bearer(OWNER)).status_code == 200
        blocked = client.post("/v1/chat/completions", headers=bearer(WORK), json={})
        assert blocked.status_code == 403 and "suspended" in blocked.text
        client.post("/admin/agents/work/resume", headers=bearer(OWNER))
        assert client.post("/v1/chat/completions", headers=bearer(WORK), json={}).status_code == 200
        assert len(upstream.requests) == 1


def test_log_never_holds_secrets(tmp_path: Path) -> None:
    with http_app(tmp_path) as (client, _, ctx):
        client.get("/v1/models", headers=bearer(WORK))
        client.post("/v1/chat/completions", headers=bearer(WORK), json={"messages": []})
        client.post("/tools/personal/mcp", headers=bearer(WORK), json={})
        client.get("/admin/agents", headers=bearer("wrong"))
        text = "\n".join(ctx.store.export_lines())
    for secret in (KEY, WORK, PERSONAL, OWNER):
        assert secret not in text
    assert make_settings(tmp_path).nebius_api_key == KEY


def test_stream_scanner_survives_split_and_garbage() -> None:
    scanner = StreamScanner()
    for i in range(0, len(STREAM_BODY), 7):
        scanner.feed(STREAM_BODY[i : i + 7])
    scanner.feed(b"data: {broken\n\n")
    assert scanner.usage["total_tokens"] == 12
    assert scanner.tool_calls == ["ask"]
