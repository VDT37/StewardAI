"""The public demo: the scripted evening bundled, served read-only, and the playground."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from gateway_helpers import ROOT
from starlette.requests import Request

from steward_gateway.cli import main
from steward_gateway.demo import (
    MAX_BODY,
    Buckets,
    client_address,
    create_demo_app,
    secret_problems,
)
from steward_gateway.scripted import record_scripted


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The scripted evening, recorded and bundled the way `steward demo` does it."""
    mp = pytest.MonkeyPatch()
    mp.setenv("STEWARD_DEMO_ANCHOR", "replay")
    folder = tmp_path_factory.mktemp("demo")
    events = asyncio.run(record_scripted(ROOT / "config" / "steward.yaml"))
    export = folder / "export.jsonl"
    export.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    code = main(
        [
            "demo",
            "bundle",
            "--export",
            str(export),
            "--name",
            "scripted",
            "--label",
            "Scripted actors through the same doors",
            "--kind",
            "scripted",
            "--dir",
            str(folder / "bundle"),
        ]
    )
    mp.undo()
    assert code == 0
    return folder / "bundle"


def demo_client(bundle: Path) -> TestClient:
    return TestClient(create_demo_app(bundle, None, Buckets(per_minute=6000, burst=1000)))


def test_visitor_reads_the_replayed_evening_and_cannot_change_it(bundle: Path) -> None:
    with demo_client(bundle) as client:
        assert client.get("/healthz").json()["recordings"] == 1
        info = client.get("/demo/info").json()
        assert info["default"] == "scripted"
        rec = info["recordings"][0]
        assert rec["replay"]["ok"] and rec["replay"]["compared"] >= 70
        assert rec["kind"] == "scripted" and rec["models_used"] == {}
        assert rec["brief"]["source"] == "template"
        assert any(b["beat"] == "21:15 new mail" for b in rec["beats"])

        status = client.get("/r/scripted/admin/status").json()
        assert status["mode"] == "demo"
        head = status["head"]["seq"]
        # Reading every page writes nothing.
        for path in (
            "/admin/holds?state=all",
            "/admin/holds",
            "/admin/exchanges",
            "/admin/agents",
            "/admin/agents/work",
            "/admin/brief",
            "/admin/mail",
            "/admin/watcher",
            "/admin/log?limit=50",
            "/admin/log/verify",
            "/admin/policy",
            "/admin/stand-ins",
        ):
            assert client.get("/r/scripted" + path).status_code == 200, path
        assert client.get("/r/scripted/admin/status").json()["head"]["seq"] == head
        brief = client.get("/r/scripted/admin/brief").json()
        assert brief["question"]["kind"] == "change_paid"

        # Owner actions, the model door and the tool door are out of reach.
        hold = client.get("/r/scripted/admin/holds").json()[0]
        for path in (
            "/admin/agents/work/suspend",
            "/admin/agents/work/revoke",
            "/admin/agents/work/tools/web_extract/off",
            f"/admin/holds/{hold['hold_id']}/decide",
            "/admin/brief/run",
            "/admin/replay",
            "/admin/scenario/run",
        ):
            assert client.post("/r/scripted" + path, json={}).status_code == 403, path
        assert client.get("/r/scripted/v1/models").status_code == 404
        assert client.get("/r/scripted/tools/work/mcp").status_code == 404
        assert client.get("/r/other/admin/status").status_code == 404
        assert client.get("/r/scripted/admin/status").json()["head"]["seq"] == head


def check(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/demo/check", json=body)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def test_playground_runs_stewards_own_checks(bundle: Path) -> None:
    with demo_client(bundle) as client:
        examples = client.get("/demo/info").json()["examples"]
        reply = next(e for e in examples["reply"] if e["request_type"] == "availability")
        got = check(client, {"kind": "reply", **reply})
        assert got["decision"] == "allow"
        assert "Dr Shah" in " ".join(got["planted_in_reply"]) or got["planted_in_reply"]
        assert got["planted_crossed"] == []
        assert {"title", "location", "note"} <= set(got["dropped_fields"])
        assert "busy" in got["asker_reads"]

        reasons = check(client, {"kind": "ask", **examples["ask"][0]})
        assert reasons["decision"] == "refuse" and reasons["rule"] == "unknown_request_type"
        extra = check(client, {"kind": "ask", **examples["ask"][1]})
        assert extra["decision"] == "refuse" and extra["rule"] == "invalid_fields"
        fine = check(client, {"kind": "ask", **examples["ask"][2]})
        assert fine["decision"] == "allow" and "2026-10-12" in fine["message_to_personal"]

        mail = check(client, {"kind": "mail", "message": examples["mail"]})
        assert mail["suspected"] and "posing_as_admin" in mail["signs"]
        assert "[LINK-1 withheld by Steward]" in mail["agent_reads"]["body"]
        plain = check(
            client,
            {"kind": "mail", "message": {"from": "Olivia <o@brightwater.example>", "body": "Hi"}},
        )
        assert not plain["suspected"]

        lock = examples["lock"]
        same = check(
            client, {"kind": "lock", "hold": lock["hold"], "edited_args": lock["hold"]["args"]}
        )
        assert same["matches"] and same["approved_full"] == lock["fingerprint"]
        edited = {**lock["hold"]["args"], "booking_id": "bk-9"}
        void = check(client, {"kind": "lock", "hold": lock["hold"], "edited_args": edited})
        assert not void["matches"]

        assert client.post("/demo/check", json={"kind": "shell"}).status_code == 422
        big = {"kind": "mail", "message": {"body": "x" * (MAX_BODY + 1)}}
        assert client.post("/demo/check", json=big).status_code == 413


def test_rerun_replay_passes_and_is_cached(bundle: Path) -> None:
    with demo_client(bundle) as client:
        first = client.post("/demo/replay", json={"recording": "scripted"}).json()
        assert first["ok"] and not first["cached"]
        again = client.post("/demo/replay", json={"recording": "scripted"}).json()
        assert again["cached"] and again["compared"] == first["compared"]
        assert client.post("/demo/replay", json={"recording": "nope"}).status_code == 404


def test_rate_limit_per_client() -> None:
    now = [0.0]
    buckets = Buckets(per_minute=30, burst=10, now=lambda: now[0])
    assert all(buckets.take("a") for _ in range(10))
    assert not buckets.take("a")
    assert buckets.take("b")
    now[0] += 2.0  # 30 a minute refills one token every 2 s
    assert buckets.take("a") and not buckets.take("a")


def test_forwarded_address_only_from_a_private_peer() -> None:
    def request(peer: str, forwarded: str | None) -> Request:
        headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
        return Request({"type": "http", "client": (peer, 1), "headers": headers})

    assert client_address(request("127.0.0.1", "203.0.113.9")) == "203.0.113.9"
    assert client_address(request("172.17.0.1", "1.2.3.4, 203.0.113.9")) == "203.0.113.9"
    assert client_address(request("198.51.100.7", "203.0.113.9")) == "198.51.100.7"
    assert client_address(request("8.8.8.8", "203.0.113.9")) == "8.8.8.8"


def test_secret_scan_refuses_markers_and_env_values() -> None:
    env = {"NEBIUS_API_KEY": "nb-0123456789abcdef", "STEWARD_PASS_WORK": "x7y8z9w0q1"}
    assert secret_problems('{"ok": true}', env) == []
    assert secret_problems("token stw_abc", {}) == ["contains the marker 'stw_'"]
    found = secret_problems("key nb-0123456789abcdef and x7y8z9w0q1", env)
    assert found == [
        "contains the value of NEBIUS_API_KEY",
        "contains the value of STEWARD_PASS_WORK",
    ]
