"""Four chat-completion checks against one model: plain, stream, tools, stream plus tools.

Phase 0, Part F. Point --base-url at the stub to prove pass-through:

    uv run python spikes/token_factory_check.py --model "$MODEL_WORK"
    uv run python spikes/token_factory_check.py --base-url http://127.0.0.1:8000/v1 \
        --key-env STEWARD_PASS_WORK --model "$STEWARD_MODEL_WORK"

Exits non-zero if any check fails. Never prints the bearer token.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1/"
TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
TOOL_PROMPT = "Am I busy anywhere between 2026-10-12 and 2026-10-15? Use the get_availability tool."
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_availability",
            "description": (
                "Return the user's busy blocks between two dates (inclusive, format YYYY-MM-DD)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "First date, YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "Last date, YYYY-MM-DD"},
                },
                "required": ["start_date", "end_date"],
            },
        },
    }
]
TOOL_RESULT = {"busy": [{"date": "2026-10-14", "start": "14:00", "end": "16:00"}]}
REASONING_KEYS = ("reasoning_content", "reasoning")


@dataclass
class Result:
    name: str
    passed: bool = False
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    notes: list[str] = field(default_factory=list)

    def add_usage(self, usage: Any) -> None:
        if isinstance(usage, dict):
            self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(usage.get("completion_tokens") or 0)


@dataclass
class Pricing:
    per_m_in: float | None = None
    per_m_out: float | None = None
    context_length: int | None = None

    def cost(self, result: Result) -> str:
        if self.per_m_in is None or self.per_m_out is None:
            return "n/a"
        usd = result.prompt_tokens * self.per_m_in + result.completion_tokens * self.per_m_out
        return f"${usd / 1_000_000:.6f}"


def usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4g}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Token Factory or stub pass-through checks.")
    parser.add_argument("--base-url", default=TOKEN_FACTORY_URL)
    parser.add_argument("--model", required=True)
    parser.add_argument("--key-env", default="NEBIUS_API_KEY")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--price-in", type=float, help="USD per million input tokens")
    parser.add_argument("--price-out", type=float, help="USD per million output tokens")
    return parser.parse_args()


def short(value: Any, limit: int = 200) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def fetch_pricing(client: httpx.Client, model: str, args: argparse.Namespace) -> Pricing:
    pricing = Pricing(per_m_in=args.price_in, per_m_out=args.price_out)
    try:
        response = client.get("/models", params={"verbose": "true"})
        listed = response.json().get("data", []) if response.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        listed = []
    entry = next((m for m in listed if isinstance(m, dict) and m.get("id") == model), None)
    if entry is None:
        print(f"model {model} not in the target's model list, so no pricing or context length")
        return pricing
    if isinstance(entry.get("context_length"), int):
        pricing.context_length = entry["context_length"]
    served = entry.get("pricing") or {}
    try:
        if pricing.per_m_in is None and served.get("prompt") is not None:
            pricing.per_m_in = float(served["prompt"]) * 1_000_000
        if pricing.per_m_out is None and served.get("completion") is not None:
            pricing.per_m_out = float(served["completion"]) * 1_000_000
    except (TypeError, ValueError):
        pass
    return pricing


def sse_events(response: httpx.Response) -> tuple[list[dict[str, Any]], list[str]]:
    """Parsed JSON data events and the raw data payloads, in order."""
    events: list[dict[str, Any]] = []
    raw: list[str] = []
    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        raw.append(payload)
        if payload.startswith("{"):
            event = json.loads(payload)
            if isinstance(event, dict):
                events.append(event)
    return events, raw


def first_choice(body: dict[str, Any]) -> dict[str, Any]:
    choices = body.get("choices") or [{}]
    choice: dict[str, Any] = choices[0]
    return choice


def check_plain(client: httpx.Client, model: str, max_tokens: int, result: Result) -> None:
    response = client.post(
        "/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "user", "content": "Reply with one short sentence confirming you work."}
            ],
            "max_tokens": max_tokens,
        },
    )
    if response.status_code != 200:
        result.notes.append(f"HTTP {response.status_code}: {short(response.text)}")
        return
    body = response.json()
    choice = first_choice(body)
    message = choice.get("message") or {}
    result.add_usage(body.get("usage"))
    usage = body.get("usage") or {}
    reasoning = [k for k in REASONING_KEYS if message.get(k)]
    result.notes.append(f"message keys: {sorted(message)}")
    if reasoning:
        result.notes.append(f"reasoning field: {reasoning[0]}")
    limits = {
        k: v for k, v in response.headers.items() if k.lower().startswith("x-ratelimit-remaining")
    }
    result.notes.append(f"rate limits: {limits or 'none'}")
    if choice.get("finish_reason") != "stop":
        result.notes.append(f"finish_reason: {choice.get('finish_reason')}")
    result.passed = (
        bool((message.get("content") or "").strip())
        and choice.get("finish_reason") == "stop"
        and all(k in usage for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
    )


def check_stream(client: httpx.Client, model: str, max_tokens: int, result: Result) -> None:
    started = time.perf_counter()
    request = {
        "model": model,
        "messages": [{"role": "user", "content": "Count from 1 to 5, separated by commas."}],
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    with client.stream("POST", "/chat/completions", json=request) as response:
        if response.status_code != 200:
            response.read()
            result.notes.append(f"HTTP {response.status_code}: {short(response.text)}")
            return
        events, raw = sse_events(response)
    texts = [
        (e["choices"][0].get("delta") or {}).get("content") or ""
        for e in events
        if e.get("choices")
    ]
    content_chunks = [t for t in texts if t]
    usage_chunks = [e for e in events if e.get("usage") and not e.get("choices")]
    if usage_chunks:
        result.add_usage(usage_chunks[-1]["usage"])
    joined = "".join(content_chunks)
    result.notes.append(
        f"{len(raw)} data lines, {len(content_chunks)} with text: {short(joined, 60)}"
    )
    if not usage_chunks:
        result.notes.append("no usage chunk with empty choices")
    if not raw or raw[-1] != "[DONE]":
        result.notes.append("stream did not end with [DONE]")
    result.notes.append(f"total {round((time.perf_counter() - started) * 1000)} ms")
    result.passed = (
        len(content_chunks) >= 2
        and bool(joined.strip())
        and bool(usage_chunks)
        and raw[-1:] == ["[DONE]"]
    )


def valid_arguments(arguments: Any) -> bool:
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) else None
    except ValueError:
        return False
    return isinstance(parsed, dict) and {"start_date", "end_date"} <= parsed.keys()


def check_tools(client: httpx.Client, model: str, max_tokens: int, result: Result) -> None:
    messages: list[dict[str, Any]] = [{"role": "user", "content": TOOL_PROMPT}]
    turn1 = client.post(
        "/chat/completions",
        json={
            "model": model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "max_tokens": max_tokens,
        },
    )
    if turn1.status_code != 200:
        result.notes.append(f"turn 1 HTTP {turn1.status_code}: {short(turn1.text)}")
        return
    body1 = turn1.json()
    result.add_usage(body1.get("usage"))
    choice = first_choice(body1)
    message = choice.get("message") or {}
    calls = message.get("tool_calls") or []
    call = calls[0] if calls else {}
    function = call.get("function") or {}
    turn1_ok = (
        choice.get("finish_reason") == "tool_calls"
        and function.get("name") == "get_availability"
        and valid_arguments(function.get("arguments"))
    )
    result.notes.append(
        f"turn 1: finish_reason={choice.get('finish_reason')} "
        f"call={function.get('name')} args={short(function.get('arguments'), 80)}"
    )
    if not turn1_ok:
        return

    messages += [
        {"role": "assistant", "content": message.get("content") or "", "tool_calls": calls},
        {"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(TOOL_RESULT)},
    ]
    turn2 = client.post(
        "/chat/completions",
        json={"model": model, "messages": messages, "tools": TOOLS, "max_tokens": max_tokens},
    )
    if turn2.status_code != 200:
        result.notes.append(f"turn 2 HTTP {turn2.status_code}: {short(turn2.text)}")
        return
    body2 = turn2.json()
    result.add_usage(body2.get("usage"))
    choice2 = first_choice(body2)
    answer = (choice2.get("message") or {}).get("content") or ""
    mentions = "14" in answer and ("2" in answer or "14:00" in answer)
    result.notes.append(
        f"turn 2: finish_reason={choice2.get('finish_reason')} "
        f"mentions 14 Oct 2pm: {mentions}; {short(answer, 80)}"
    )
    result.passed = choice2.get("finish_reason") == "stop" and bool(answer.strip())


def check_stream_tools(client: httpx.Client, model: str, max_tokens: int, result: Result) -> None:
    request = {
        "model": model,
        "messages": [{"role": "user", "content": TOOL_PROMPT}],
        "tools": TOOLS,
        "tool_choice": "auto",
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    with client.stream("POST", "/chat/completions", json=request) as response:
        if response.status_code != 200:
            response.read()
            result.notes.append(f"HTTP {response.status_code}: {short(response.text)}")
            return
        events, _ = sse_events(response)
    fragments = [
        tc
        for e in events
        if e.get("choices")
        for tc in ((e["choices"][0].get("delta") or {}).get("tool_calls") or [])
    ]
    usage_chunks = [e for e in events if e.get("usage") and not e.get("choices")]
    if usage_chunks:
        result.add_usage(usage_chunks[-1]["usage"])
    calls: dict[int, dict[str, str]] = {}
    for fragment in fragments:
        index = fragment.get("index", 0)
        call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        function = fragment.get("function") or {}
        call["id"] = call["id"] or fragment.get("id") or ""
        call["name"] = call["name"] or function.get("name") or ""
        call["arguments"] += function.get("arguments") or ""
    missing_index = sum(1 for f in fragments if "index" not in f)
    named = sum(1 for f in fragments if (f.get("function") or {}).get("name"))
    result.notes.append(
        f"{len(fragments)} tool-call chunks, index missing on {missing_index}, "
        f"name on {named}; calls={[(c['name'], short(c['arguments'], 60)) for c in calls.values()]}"
    )
    result.passed = (
        len(calls) == 1
        and next(iter(calls.values()))["name"] == "get_availability"
        and valid_arguments(next(iter(calls.values()))["arguments"])
    )


CHECKS: list[tuple[str, Callable[[httpx.Client, str, int, Result], None]]] = [
    ("plain", check_plain),
    ("stream", check_stream),
    ("tools", check_tools),
    ("stream+tools", check_stream_tools),
]


def main() -> int:
    args = parse_args()
    token = os.environ.get(args.key_env, "")
    if not token:
        print(f"{args.key_env} is not set", file=sys.stderr)
        return 2
    base_url = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    results: list[Result] = []
    with httpx.Client(base_url=base_url, headers=headers, timeout=TIMEOUT) as client:
        pricing = fetch_pricing(client, args.model, args)
        print(
            f"target {base_url}  model {args.model}  context_length {pricing.context_length}  "
            f"price in/out per M: {usd(pricing.per_m_in)} / {usd(pricing.per_m_out)}"
        )
        for name, check in CHECKS:
            result = Result(name)
            started = time.perf_counter()
            try:
                check(client, args.model, args.max_tokens, result)
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
                result.notes.append(f"error: {type(exc).__name__}: {short(str(exc))}")
            result.latency_ms = round((time.perf_counter() - started) * 1000)
            results.append(result)
            print(
                f"{name:<13} {'PASS' if result.passed else 'FAIL'}  {result.latency_ms:>6} ms  "
                f"prompt={result.prompt_tokens} completion={result.completion_tokens}  "
                f"cost={pricing.cost(result)}"
            )
            for note in result.notes:
                print(f"    {note}")
    failed = [r.name for r in results if not r.passed]
    print("ALL PASS" if not failed else f"FAILED: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
