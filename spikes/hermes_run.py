# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Start a Hermes run through its API server and report the tool events and the output.

Phase 0, Part I step 8. Runs on the VM host and reaches the Hermes API server inside the
work-agent sandbox through the OpenShell port forward (127.0.0.1:8642).

    HERMES_API_KEY=... uv run spikes/hermes_run.py --key-env HERMES_API_KEY --prompt "..."

Exits 0 only if the run completed and at least one tool event was seen.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any

import httpx

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}
TOOL_EVENTS = {"tool.started", "tool.completed"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start a Hermes run and report its tool events.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8642")
    parser.add_argument("--key-env", default="API_SERVER_KEY")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--session-id", default="phase0-spike")
    parser.add_argument("--timeout", type=float, default=180.0, help="seconds for the whole run")
    parser.add_argument("--raw", action="store_true", help="print every raw SSE line")
    return parser.parse_args()


def find(data: Any, keys: tuple[str, ...]) -> Any:
    """Return the first matching key at the top level or one level down."""
    if not isinstance(data, dict):
        return None
    for key in keys:
        if data.get(key) not in (None, ""):
            return data[key]
    for value in data.values():
        if isinstance(value, dict):
            for key in keys:
                if value.get(key) not in (None, ""):
                    return value[key]
    return None


def short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def start_run(client: httpx.Client, prompt: str, session_id: str) -> str:
    response = client.post(
        "/v1/runs",
        json={"input": prompt, "session_id": session_id},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    if response.status_code >= 400:
        sys.exit(f"POST /v1/runs failed: HTTP {response.status_code} {short(response.text)}")
    run_id = find(response.json(), ("run_id", "id"))
    if not run_id:
        sys.exit(f"POST /v1/runs returned no run_id: {short(response.text)}")
    print(f"run_id: {run_id} (HTTP {response.status_code})")
    return str(run_id)


def stream_events(client: httpx.Client, run_id: str, deadline: float, raw: bool) -> list[str]:
    """Read the SSE stream until a terminal event, the end of the stream, or the deadline.

    Returns the names of the tools seen in tool events.
    """
    tools: list[str] = []
    event_name: str | None = None
    data_lines: list[str] = []

    def dispatch() -> bool:
        nonlocal event_name, data_lines
        payload_text = "\n".join(data_lines)
        payload: Any = None
        if payload_text:
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                payload = payload_text
        name = event_name or find(payload, ("event", "type")) or "message"
        event_name, data_lines = None, []
        if name in TOOL_EVENTS:
            tool = find(payload, ("tool_name", "name", "tool")) or "?"
            preview = find(payload, ("preview", "arguments", "args", "result"))
            if name == "tool.started":
                tools.append(str(tool))
            print(f"{name}: {tool}" + (f"  {short(preview)}" if preview is not None else ""))
        elif name in TERMINAL_EVENTS:
            print(name)
            return True
        return False

    with client.stream("GET", f"/v1/runs/{run_id}/events") as response:
        if response.status_code >= 400:
            response.read()
            print(f"events stream failed: HTTP {response.status_code} {short(response.text)}")
            return tools
        for line in response.iter_lines():
            if raw:
                print(f"  raw| {line}")
            if time.monotonic() > deadline:
                print("deadline reached while streaming events")
                break
            if line.startswith(":"):
                continue  # keepalive comment
            if line == "":
                if (event_name or data_lines) and dispatch():
                    break
                continue
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                event_name = value
            elif field == "data":
                data_lines.append(value)
        else:
            if event_name or data_lines:
                dispatch()
    return tools


def poll_run(client: httpx.Client, run_id: str, deadline: float) -> dict[str, Any]:
    while True:
        response = client.get(f"/v1/runs/{run_id}")
        if response.status_code >= 400:
            sys.exit(f"GET /v1/runs/{run_id} failed: HTTP {response.status_code}")
        run: dict[str, Any] = response.json()
        if run.get("status") in TERMINAL_STATUSES or time.monotonic() > deadline:
            return run
        time.sleep(2)


def main() -> int:
    args = parse_args()
    key = os.environ.get(args.key_env, "")
    if not key:
        print(f"{args.key_env} is not set", file=sys.stderr)
        return 2

    deadline = time.monotonic() + args.timeout
    timeout = httpx.Timeout(connect=10.0, read=args.timeout, write=10.0, pool=10.0)
    headers = {"Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=timeout) as client:
        run_id = start_run(client, args.prompt, args.session_id)
        tools = stream_events(client, run_id, deadline, args.raw)
        run = poll_run(client, run_id, deadline)

    status = run.get("status")
    print(f"status: {status}")
    print(f"tools: {', '.join(tools) if tools else 'none'}")
    if run.get("usage") is not None:
        print(f"usage: {json.dumps(run['usage'])}")
    output = run.get("output")
    print("output:")
    print(output if isinstance(output, str) else json.dumps(output))

    if status != "completed":
        print("FAIL: run did not complete", file=sys.stderr)
        return 1
    if not tools:
        print("FAIL: no tool event seen", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
