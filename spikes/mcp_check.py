"""Check one side of the stub's MCP tool door (Phase 0, Part G).

Connects with the MCP SDK's Streamable HTTP client, lists the tools, calls
list_calendar_events, and checks that a wrong pass gets 401.

    uv run --package steward-gateway python spikes/mcp_check.py \
        --url http://127.0.0.1:8000/tools/work/mcp --pass-env STEWARD_PASS_WORK

Exits non-zero on any failure. Never prints the pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx
import httpx2
from mcp.client import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

TOOL = "list_calendar_events"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP tool door check for one side.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--pass-env", required=True)
    parser.add_argument("--start-date", default="2026-10-12")
    parser.add_argument("--end-date", default="2026-10-15")
    return parser.parse_args()


async def call_tool(url: str, token: str, start: str, end: str) -> tuple[list[str], CallToolResult]:
    # The 5 s httpx default is too short for a tool call; the SDK reads the stream for longer.
    timeout = httpx2.Timeout(30.0, read=300.0)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=timeout) as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                result = await session.call_tool(TOOL, {"start_date": start, "end_date": end})
    if not isinstance(result, CallToolResult):
        raise RuntimeError(f"unexpected result type {type(result).__name__}")
    return [tool.name for tool in listed.tools], result


def wrong_pass_status(url: str) -> int:
    response = httpx.post(
        url,
        headers={"Authorization": "Bearer wrong", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        timeout=10.0,
    )
    return response.status_code


def main() -> int:
    args = parse_args()
    token = os.environ.get(args.pass_env, "")
    if not token:
        print(f"{args.pass_env} is not set", file=sys.stderr)
        return 2
    failures: list[str] = []

    try:
        names, result = asyncio.run(call_tool(args.url, token, args.start_date, args.end_date))
    except Exception as exc:
        print(f"MCP session failed: {type(exc).__name__}: {exc}")
        names, result = [], None
        failures.append("session")
    if result is not None:
        print(f"tools: {names}")
        if TOOL not in names:
            failures.append("tool missing")
        if result.is_error:
            failures.append("tool error")
        payload = result.structured_content
        if payload is None:
            texts = [c.text for c in result.content if isinstance(c, TextContent)]
            print("result (text):", " ".join(texts)[:2000])
        else:
            events = payload.get("events", [])
            print(f"result: side={payload.get('side')} events={len(events)}")
            for event in events:
                print(f"  {event['start']}  {event['end']}  {event['title']}")
            if not events:
                failures.append("no events")
        print("raw:", json.dumps(payload)[:300] if payload is not None else "n/a")

    status = wrong_pass_status(args.url)
    print(f"wrong pass: HTTP {status}")
    if status != 401:
        failures.append(f"wrong pass gave {status}")

    print("PASS" if not failures else f"FAIL: {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
