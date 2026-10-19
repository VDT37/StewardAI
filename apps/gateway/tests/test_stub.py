import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mcp.types import CallToolResult

from steward_gateway.stub import (
    Settings,
    StreamScanner,
    StubLog,
    build_tool_server,
    create_app,
)

KEY = "tf-real-key-not-for-agents"
WORK = "stw_work_pass_for_tests"
PERSONAL = "stw_personal_pass_for_tests"
ENV = {
    "NEBIUS_API_KEY": KEY,
    "STEWARD_PASS_WORK": WORK,
    "STEWARD_PASS_PERSONAL": PERSONAL,
    "STEWARD_MODEL_WORK": "nvidia/work-model",
    "STEWARD_MODEL_PERSONAL": "nvidia/small-model",
}
BASE = "http://127.0.0.1:8000"
STREAM_BODY = (
    b'data: {"choices":[{"index":0,"delta":{"content":"1, 2"}}]}\n\n'
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c1",'
    b'"function":{"name":"get_availability","arguments":"{\\"start"}}]}}]}\n\n'
    b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":7,"total_tokens":12}}\n\n'
    b"data: [DONE]\n\n"
)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_settings(tmp_path: Path) -> Settings:
    return replace(Settings.from_env(ENV), log_path=tmp_path / "stub-log.jsonl")


class Chunks(httpx.AsyncByteStream):
    """A response body that arrives in small pieces, like a real SSE stream."""

    def __init__(self, body: bytes, size: int = 11) -> None:
        self.body = body
        self.size = size

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for i in range(0, len(self.body), self.size):
            yield self.body[i : i + self.size]


class Upstream:
    """Fake Token Factory that records what the stub sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if body.get("stream"):
            return httpx.Response(
                200,
                stream=Chunks(STREAM_BODY),
                headers={
                    "content-type": "text/event-stream",
                    "x-ratelimit-remaining-requests": "59",
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{"function": {"name": "get_availability"}}],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            },
            headers={"x-ratelimit-remaining-tokens": "399000"},
        )


@pytest.fixture
def stub(tmp_path: Path) -> Any:
    upstream = Upstream()
    settings = make_settings(tmp_path)
    app = create_app(settings, transport=httpx.MockTransport(upstream))
    with TestClient(app, base_url=BASE) as client:
        yield client, upstream, settings.log_path


def log_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_settings_reject_missing_and_equal_passes() -> None:
    with pytest.raises(RuntimeError, match="NEBIUS_API_KEY"):
        Settings.from_env({**ENV, "NEBIUS_API_KEY": " "})
    with pytest.raises(RuntimeError, match="must differ"):
        Settings.from_env({**ENV, "STEWARD_PASS_PERSONAL": WORK})


def test_agent_for_maps_passes() -> None:
    settings = Settings.from_env(ENV)
    assert settings.agent_for(f"Bearer {WORK}") == "work"
    assert settings.agent_for(f"bearer {PERSONAL}") == "personal"
    assert settings.agent_for("Bearer wrong") is None
    assert settings.agent_for(WORK) is None
    assert settings.agent_for(None) is None


def test_healthz_needs_no_pass(stub: Any) -> None:
    client, _, _ = stub
    assert client.get("/healthz").json() == {"status": "ok"}


def test_models_lists_only_the_pass_model(stub: Any) -> None:
    client, _, log_path = stub
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers=bearer("wrong")).status_code == 401
    response = client.get("/v1/models", headers=bearer(WORK))
    assert [m["id"] for m in response.json()["data"]] == ["nvidia/work-model"]
    assert [line["agent"] for line in log_lines(log_path)] == [None, None, "work"]


def test_chat_forwards_with_real_key_and_forced_model(stub: Any) -> None:
    client, upstream, log_path = stub
    response = client.post(
        "/v1/chat/completions",
        headers={**bearer(PERSONAL), "Accept-Encoding": "gzip"},
        json={"model": "anything", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert response.json()["usage"]["total_tokens"] == 7
    assert response.headers["x-ratelimit-remaining-tokens"] == "399000"
    sent = upstream.requests[0]
    assert str(sent.url) == "https://api.tokenfactory.nebius.com/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert sent.headers["accept-encoding"] == "identity"
    assert json.loads(sent.content)["model"] == "nvidia/small-model"
    (line,) = log_lines(log_path)
    assert line["agent"] == "personal" and line["door"] == "model" and line["status"] == 200
    assert line["usage"]["total_tokens"] == 7 and line["tool_calls"] == ["get_availability"]


def test_chat_stream_is_relayed_byte_for_byte(stub: Any) -> None:
    client, _, log_path = stub
    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers=bearer(WORK),
        json={"messages": [], "stream": True, "stream_options": {"include_usage": True}},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        relayed = b"".join(response.iter_raw())
    assert relayed == STREAM_BODY
    (line,) = log_lines(log_path)
    assert line["stream"] is True and line["usage"]["total_tokens"] == 12
    assert line["tool_calls"] == ["get_availability"]


def test_chat_rejects_bad_pass_and_bad_body(stub: Any) -> None:
    client, upstream, _ = stub
    assert client.post("/v1/chat/completions", json={}).status_code == 401
    bad = client.post("/v1/chat/completions", headers=bearer(WORK), content=b"not json")
    assert bad.status_code == 400
    assert upstream.requests == []


def test_log_never_holds_secrets(stub: Any) -> None:
    client, _, log_path = stub
    client.get("/v1/models", headers=bearer(WORK))
    client.post("/v1/chat/completions", headers=bearer(WORK), json={"messages": []})
    client.get("/tools/personal/mcp", headers=bearer(WORK))
    text = log_path.read_text(encoding="utf-8")
    for secret in (KEY, WORK, PERSONAL):
        assert secret not in text


def test_mcp_routes_check_the_pass(stub: Any) -> None:
    client, _, log_path = stub
    assert client.post("/tools/work/mcp", json={}).status_code == 401
    assert client.post("/tools/work/mcp", json={}, headers=bearer(PERSONAL)).status_code == 403
    assert client.post("/tools/personal/mcp", json={}, headers=bearer(WORK)).status_code == 403
    statuses = [(line["agent"], line["status"]) for line in log_lines(log_path)]
    assert statuses == [(None, 401), ("personal", 403), ("work", 403)]


def test_mcp_initialize_passes_the_gate(stub: Any) -> None:
    client, _, _ = stub
    response = client.post(
        "/tools/work/mcp",
        headers={**bearer(WORK), "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
    )
    assert response.status_code == 200
    assert "steward-work" in response.text


def test_calendar_tool_returns_side_events(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("STEWARD_DEMO_ANCHOR", "replay")
    log = StubLog(tmp_path / "log.jsonl")
    server = build_tool_server("personal", log)

    async def call() -> CallToolResult:
        async with Client(server) as client:
            result = await client.call_tool(
                "list_calendar_events", {"start_date": "2026-10-14", "end_date": "2026-10-14"}
            )
            assert isinstance(result, CallToolResult)
            return result

    result = asyncio.run(call())
    assert not result.is_error
    payload = result.structured_content
    assert payload is not None and payload["side"] == "personal"
    titles = [e["title"] for e in payload["events"]]
    assert any("Cardiology" in t for t in titles)
    assert log_lines(tmp_path / "log.jsonl")[0]["agent"] == "personal"


def test_stream_scanner_survives_split_and_garbage() -> None:
    scanner = StreamScanner()
    for i in range(0, len(STREAM_BODY), 7):
        scanner.feed(STREAM_BODY[i : i + 7])
    scanner.feed(b"data: {broken\n\n")
    assert scanner.usage["total_tokens"] == 12
    assert scanner.tool_calls == ["get_availability"]
