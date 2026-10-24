"""Print the flight log compactly, straight from the SQLite file, and verify the chain.

    uv run --package steward-gateway python spikes/flight_log.py            # last 40 events
    uv run --package steward-gateway python spikes/flight_log.py --door a2a --limit 10
    uv run --package steward-gateway python spikes/flight_log.py --verify --db var/copy.db

Reads STEWARD_DB_PATH (default var/steward.db). Payloads are shortened; use the admin export
for full rows.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from steward_core.store import Event, Store


def describe(event: Event) -> str:
    p: dict[str, Any] = event.payload
    if event.kind == "model.call":
        usage = p.get("usage") or {}
        return (
            f"{p.get('model')} status={p.get('status')} prompt={usage.get('prompt_tokens')} "
            f"completion={usage.get('completion_tokens')} cost={p.get('cost_usd')} "
            f"calls={p.get('tool_calls')} a2a={p.get('a2a_request')}"
        )
    if event.kind == "tool.call":
        return (
            f"{p.get('tool')} -> {p.get('tool_id')} args={json.dumps(p.get('arguments'))} "
            f"error={p.get('is_error')} bytes={p.get('result_bytes')} a2a={p.get('a2a_request')}"
        )
    if event.kind == "a2a.reply":
        blocks = (p.get("reply_in") or {}).get("blocks") or []
        return (
            f"{p.get('request_id')} blocks_in={len(blocks)} dropped={p.get('dropped_fields')} "
            f"rules={p.get('rules_fired')} out={json.dumps(p.get('reply_out'))}"
        )
    if event.kind == "a2a.delivery":
        return (
            f"{p.get('request_id')} ok={p.get('ok')} status={p.get('status')} "
            f"exit={p.get('exit_code')} ms={p.get('duration_ms')} usage={p.get('usage')} "
            f"tools={p.get('tools')} error={p.get('error')}"
        )
    return json.dumps(p)[:240]


def main() -> int:
    parser = argparse.ArgumentParser(description="Show the Steward flight log.")
    parser.add_argument("--db", default=os.environ.get("STEWARD_DB_PATH", "var/steward.db"))
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--door")
    parser.add_argument("--kind")
    parser.add_argument("--agent")
    parser.add_argument("--verify", action="store_true", help="only recompute the chain")
    args = parser.parse_args()
    if not os.path.exists(args.db):
        print(f"no database at {args.db}", file=sys.stderr)
        return 2
    store = Store(args.db)
    if not args.verify:
        events = store.events(
            agent=args.agent, kind=args.kind, door=args.door, limit=args.limit, newest_first=True
        )
        for event in reversed(events):
            print(
                f"{event.seq:>5} {event.ts[11:23]} {event.agent or '-':<8} {event.kind:<20} "
                f"{describe(event)}"
            )
    result = store.verify()
    if result.ok:
        print(f"chain ok: {result.rows} rows, head {result.head_hash[:16]}")
        return 0
    print(f"chain BROKEN at seq {result.first_bad_seq}: {result.reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
