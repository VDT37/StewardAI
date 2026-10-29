"""The steward command (Phase 2 Part F).

    steward scenario manchester --mode live [--approvals scripted|manual|owner-first]
        [--speed 60 | --real-time | --demo-night] [--owner-wait-min 15]
    steward scenario manchester --mode replay [--recording FILE | --from-server] [--session ID]
    steward replay FILE [--session ID]
    steward demo record-scripted [--out FILE]
    steward demo bundle --export FILE [--session ID] --name NAME --label TEXT --kind live|scripted
    steward demo serve [--dir deploy/demo] [--port 8100]

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
    "tool.call",
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
                "real_time": args.real_time,
                "owner_wait_min": args.owner_wait_min,
                "plan": "demo-night" if args.demo_night else None,
            },
        )
        if response.status_code >= 400:
            print(f"could not start: HTTP {response.status_code} {response.text}", file=sys.stderr)
            return 2
    pace = (
        "the demo night plan"
        if args.demo_night
        else "real time"
        if args.real_time
        else f"speed {args.speed}"
    )
    print(f"scenario {args.name} started (approvals {args.approvals}, {pace})")
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


async def record_scripted(args: argparse.Namespace) -> int:
    from steward_gateway.scripted import record_scripted as play

    events = await play()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    print(f"scripted evening recorded: {len(events)} events, session {events[0]['session_id']}")
    print(f"saved to {out}")
    return 0


async def bundle(args: argparse.Namespace) -> int:
    """Add one session of an export to the public demo, after a secret scan and a replay."""
    from steward_gateway.demo import MANIFEST, NAME, secret_problems

    if not NAME.match(args.name):
        sys.exit("--name: lower-case letters, digits and dashes, at most 40")
    source = Path(args.export)
    events = pick_session(load_recording(source), args.session)
    session = str(events[0]["session_id"])
    lines = [
        line
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line)["session_id"] == session
    ]
    text = "\n".join(lines) + "\n"
    problems = secret_problems(text, os.environ)
    if problems:
        print("refused: the recording " + "; ".join(problems), file=sys.stderr)
        return 1
    result = await replay_events(events)
    if not result["ok"]:
        print(
            f"refused: the replay of session {session} fails at decision "
            f"{result['first_difference']['index']}",
            file=sys.stderr,
        )
        return 1
    folder = Path(args.dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{args.name}.jsonl").write_text(text, encoding="utf-8", newline="\n")
    manifest_path = folder / MANIFEST
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {"recordings": []}
    )
    entry = {
        "name": args.name,
        "file": f"{args.name}.jsonl",
        "label": args.label,
        "kind": args.kind,
        "session_id": session,
    }
    entries = [e for e in manifest["recordings"] if e["name"] != args.name]
    position = next(
        (i for i, e in enumerate(manifest["recordings"]) if e["name"] == args.name), len(entries)
    )
    entries.insert(position, entry)
    manifest["recordings"] = entries
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"bundled {args.name}: session {session}, {len(lines)} events, replay PASS "
        f"({result['compared']} decisions)"
    )
    return 0


def serve(args: argparse.Namespace) -> int:
    import logging

    import uvicorn

    from steward_gateway.demo import create_demo_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    console = Path(args.console)
    server = create_demo_app(Path(args.dir), console if console.is_dir() else None)
    uvicorn.run(server, host=args.host, port=args.port, proxy_headers=False, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="steward")
    commands = parser.add_subparsers(dest="command", required=True)

    scenario = commands.add_parser("scenario", help="play or replay a scenario")
    scenario.add_argument("name", choices=["manchester"])
    scenario.add_argument("--mode", choices=["live", "replay"], required=True)
    scenario.add_argument(
        "--approvals", choices=["scripted", "manual", "owner-first"], default="scripted"
    )
    scenario.add_argument("--speed", type=float, default=60.0)
    scenario.add_argument(
        "--real-time",
        action="store_true",
        help="play the evening at the clock's own pace (12 h 35 min), no fast-forward",
    )
    scenario.add_argument(
        "--demo-night",
        action="store_true",
        help="pace the story to the wall clock: night beats 23:45 to 02:30, the brief at 03:30",
    )
    scenario.add_argument(
        "--owner-wait-min",
        type=float,
        default=15.0,
        help="minutes to wait for the owner's decisions (manual and owner-first)",
    )
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

    demo = commands.add_parser("demo", help="the public demo: record, bundle and serve")
    steps = demo.add_subparsers(dest="step", required=True)
    scripted = steps.add_parser("record-scripted", help="play the evening with scripted actors")
    scripted.add_argument("--out", default="var/scripted-export.jsonl")
    packed = steps.add_parser("bundle", help="add a recorded session to deploy/demo")
    packed.add_argument("--export", required=True, help="JSONL export holding the session")
    packed.add_argument("--session", help="session id (default: the last scenario run)")
    packed.add_argument("--name", required=True)
    packed.add_argument("--label", required=True)
    packed.add_argument("--kind", choices=["live", "scripted"], required=True)
    packed.add_argument("--dir", default="deploy/demo")
    served = steps.add_parser("serve", help="serve the bundled recordings read-only")
    served.add_argument("--dir", default="deploy/demo")
    served.add_argument("--console", default="apps/web/dist")
    served.add_argument("--host", default="127.0.0.1")
    served.add_argument("--port", type=int, default=8100)

    args = parser.parse_args(argv)
    if args.command == "demo":
        if args.step == "serve":
            return serve(args)
        if args.step == "bundle":
            return asyncio.run(bundle(args))
        return asyncio.run(record_scripted(args))
    if args.command == "replay" or args.mode == "replay":
        return asyncio.run(replay(args))
    return asyncio.run(live(args))


if __name__ == "__main__":
    sys.exit(main())
