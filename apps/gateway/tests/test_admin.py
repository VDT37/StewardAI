import json
from pathlib import Path

from gateway_helpers import OWNER, WORK, bearer, http_app

OWNER_H = bearer(OWNER)


def test_admin_needs_the_owner_token(tmp_path: Path) -> None:
    with http_app(tmp_path) as (client, _, _):
        assert client.get("/admin/agents").status_code == 401
        assert client.get("/admin/agents", headers=bearer(WORK)).status_code == 401
        assert client.get("/admin/agents", headers=OWNER_H).status_code == 200


def test_agent_detail_shows_tools_pins_and_routes(tmp_path: Path) -> None:
    with http_app(tmp_path) as (client, _, _):
        work = client.get("/admin/agents/work", headers=OWNER_H).json()
        assert client.get("/admin/agents/ghost", headers=OWNER_H).status_code == 404
    assert work["pass"] == "active" and work["model"] == "nvidia/nemotron-3-super-120b-a12b"
    states = {t["name"]: t["state"] for t in work["tool_list"]}
    assert states == {
        "calendar_list_events": "pinned",
        "mail_list_messages": "pinned",
        "mail_read_message": "pinned",
        "todo_list_items": "pinned",
        "ask": "built-in",
    }
    assert work["routes"] == [
        {
            "to": "personal",
            "request_types": ["availability"],
            "detail_minutes": 30,
            "daily_limit": 10,
        }
    ]


def test_log_verify_export_and_stream(tmp_path: Path) -> None:
    with http_app(tmp_path) as (client, _, ctx):
        client.post("/v1/chat/completions", headers=bearer(WORK), json={"messages": []})
        rows = client.get("/admin/log", params={"kind": "model.call"}, headers=OWNER_H).json()
        assert [r["agent"] for r in rows] == ["work"]
        verify = client.get("/admin/log/verify", headers=OWNER_H).json()
        assert verify["ok"] and verify["rows"] >= 3 and verify["first_bad_seq"] is None
        export = client.get("/admin/log/export.jsonl", headers=OWNER_H).text.splitlines()
        assert [json.loads(line)["seq"] for line in export] == list(range(1, len(export) + 1))
        stream = client.get(
            "/admin/log/stream", params={"after_seq": 1, "follow": "false"}, headers=OWNER_H
        )
        assert stream.headers["content-type"].startswith("text/event-stream")
        data = [
            json.loads(line[5:]) for line in stream.text.splitlines() if line.startswith("data:")
        ]
        assert [d["seq"] for d in data] == list(range(2, len(export) + 1))
        assert ctx.store.head()[0] == len(export)


def test_session_tools_and_pending(tmp_path: Path) -> None:
    with http_app(tmp_path) as (client, _, ctx):
        before = ctx.recorder.session_id
        new = client.post("/admin/session", json={"label": "rehearsal"}, headers=OWNER_H).json()
        assert new["session_id"] != before and ctx.recorder.session_id == new["session_id"]
        tools = client.get("/admin/tools", headers=OWNER_H).json()
        assert {t["state"] for t in tools} == {"pinned"} and len(tools) == 7
        refreshed = client.post("/admin/tools/refresh", headers=OWNER_H).json()
        assert len(refreshed) == 7
        repin = client.post("/admin/tools/shared_todo.list_items/repin", headers=OWNER_H)
        assert repin.json()["state"] == "pinned"
        assert client.post("/admin/tools/nope.x/repin", headers=OWNER_H).status_code == 404
        assert client.get("/admin/pending", headers=OWNER_H).json() == []
        status = client.get("/admin/status", headers=OWNER_H).json()
        assert status["checker"] == "deterministic" and status["demo_anchor"] == "2026-10-12"
        kinds = [e.kind for e in ctx.store.events(door="admin", newest_first=False)]
    assert kinds == ["admin.tools_refreshed", "admin.tool_repinned"]
