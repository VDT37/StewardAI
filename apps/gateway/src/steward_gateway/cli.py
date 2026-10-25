"""The steward command (Phase 2 Part F).

    steward scenario manchester --mode live [--approvals scripted|manual] [--speed 60]
    steward scenario manchester --mode replay [--recording FILE | --from-server] [--session ID]
    steward replay FILE [--session ID]

Live mode asks the running Steward (owner token from STEWARD_OWNER_TOKEN) to play the evening,
then follows the flight log and prints each beat and decision until the run ends. Replay needs
no server and no keys: it reads an export (GET /admin/log/export.jsonl), or downloads one with
--from-server, and plays the scenario session back in memory. Exit code 0 means the decisions
matched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from steward_core.clock import LONDON, parse_iso
from steward_gateway.replay import load_recording, pick_session, replay_events

DEFAULT_URL = "http://127.0.0.1:8000"
SHOWN = (
    "scenario.",
    "a2a.request",
    "a2a.reply",
    "a2a.refused",
    "a2a.held",
    "a2a.notice",
    "stand_in.issued",
    "tool.held",
    "tool.refused",
    "tool.denied",
    "hold.",
    "intent.",
    "model.budget_refused",
    "world.change",
)


def _token() -> str:
    token = os.environ.get("STEWARD_OWNER_TOKEN", "").strip()
    if not token:
        sys.exit("STEWARD_OWNER_TOKEN is not set (run with --env-file .env)")
    return token


def line(event: dict[str, Any]) -> str:
    p = event["payload"]
    # Story time is shown in London time, as the story is written.
    time = parse_iso(str(event["ts"])).astimezone(LONDON).strftime("%a %H:%M:%S")
    who = event.get("agent") or "-"
    kind = event["kind"]
    detail = ""
    if kind == "scenario.beat":
        detail = f"{p.get('beat')} ({p.get('actor')})"
    elif "call" in p:
        call = p["call"]
        detail = f"{call.get('tool')} {json.dumps(call.get('arguments'))[:90]}"
    if p.get("rule"):
        detail += f" -> {p['rule']}"
    if p.get("hold_id") and kind.startswith(("tool.held", "a2a.held", "hold.")):
        detail += f" [{p['hold_id']}]"
    if kind == "a2a.reply":
        detail = (
            f"crossed {json.dumps(p.get('reply_out'))[:110]}; dropped {p.get('dropped_fields')}"
        )
    if kind == "a2a.notice":
        detail = json.dumps(p.get("notices"))[:120]
    if kind == "world.change":
        detail = str(p.get("id"))
    return f"{time}  {who:<8} {kind:<20} {detail}"


async def follow(base_url: str, token: str, after_seq: int) -> int:
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    async with (
        httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout) as client,
        client.stream("GET", f"/admin/log/stream?after_seq={after_seq}") as response,
    ):
        response.raise_for_status()
        async for raw in response.aiter_lines():
            if not raw.startswith("data:"):
                continue
            event = json.loads(raw[5:].strip())
            if event["kind"].startswith(SHOWN):
                print(line(event), flush=True)
            if event["kind"] in {"scenario.end", "scenario.failed"}:
                return 0 if event["kind"] == "scenario.end" else 1
    return 1


async def live(args: argparse.Namespace) -> int:
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=30) as client:
        status = (await client.get("/admin/status")).json()
        after = int(status["head"]["seq"])
        response = await client.post(
            "/admin/scenario/run",
            json={
                "name": args.name,
                "approvals": args.approvals,
                "speed": args.speed,
                "attacks": args.attacks,
            },
        )
        if response.status_code >= 400:
            print(f"could not start: HTTP {response.status_code} {response.text}", file=sys.stderr)
            return 2
    print(f"scenario {args.name} started (approvals {args.approvals}, speed {args.speed})")
    return await follow(args.base_url, token, after)


async def replay(args: argparse.Namespace) -> int:
    if args.from_server:
        token = _token()
        async with httpx.AsyncClient(
            base_url=args.base_url, headers={"Authorization": f"Bearer {token}"}, timeout=60
        ) as client:
            response = await client.get("/admin/log/export.jsonl")
            response.raise_for_status()
        path = Path(args.recording or "var/steward-export.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        print(f"export saved to {path}")
    elif args.recording:
        path = Path(args.recording)
    else:
        sys.exit("give --recording FILE or --from-server")
    events = pick_session(load_recording(path), args.session)
    result = await replay_events(events)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if result["ok"]:
        print(
            f"PASS: {result['compared']} decision events matched "
            f"(session {result['session_id']}, {result['seconds']} s)"
        )
        return 0
    print(f"FAIL at decision {result['first_difference']['index']} of {result['compared']}")
    print(json.dumps(result["first_difference"], indent=2)[:4000])
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="steward")
    commands = parser.add_subparsers(dest="command", required=True)

    scenario = commands.add_parser("scenario", help="play or replay a scenario")
    scenario.add_argument("name", choices=["manchester"])
    scenario.add_argument("--mode", choices=["live", "replay"], required=True)
    scenario.add_argument("--approvals", choices=["scripted", "manual"], default="scripted")
    scenario.add_argument("--speed", type=float, default=60.0)
    scenario.add_argument(
        "--attacks", choices=["fallback", "agent", "scripted"], default="fallback"
    )
    scenario.add_argument("--base-url", default=DEFAULT_URL)
    scenario.add_argument("--recording", help="JSONL export to replay (or where to save one)")
    scenario.add_argument("--from-server", action="store_true", help="download the export first")
    scenario.add_argument("--session", help="session to replay (default: the last scenario run)")
    scenario.add_argument("--out", default="var/replay-result.json")

    again = commands.add_parser("replay", help="replay a session from a JSONL export")
    again.add_argument("recording")
    again.add_argument("--session")
    again.add_argument("--out", default="var/replay-result.json")
    again.add_argument("--from-server", action="store_true")
    again.add_argument("--base-url", default=DEFAULT_URL)

    args = parser.parse_args(argv)
    if args.command == "replay" or args.mode == "replay":
        return asyncio.run(replay(args))
    return asyncio.run(live(args))


if __name__ == "__main__":
    sys.exit(main())
