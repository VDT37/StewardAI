"""Model door helpers: what a call cost, and exact-string masking before a call leaves."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from steward_core.policy import Price

MILLION = Decimal(1_000_000)
COST_PLACES = Decimal("0.000001")


def call_cost(usage: Any, price: Price | None) -> str | None:
    """USD for one call as a decimal string (6 places), or None when usage or price is missing."""
    if price is None or not isinstance(usage, Mapping):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    cost = (prompt * price.input_per_million + completion * price.output_per_million) / MILLION
    return str(cost.quantize(COST_PLACES))


def mask_body(body: Mapping[str, Any], entries: Sequence[str]) -> tuple[dict[str, Any], list[int]]:
    """Replace each mask entry with [MASKED-n] in message text and tool-call arguments.

    Returns the new body and the 1-based numbers of the entries that fired (never the strings,
    so the log does not hold what was masked).
    """
    result = copy.deepcopy(dict(body))
    fired: set[int] = set()
    if not entries:
        return result, []

    def scrub(text: str) -> str:
        for number, entry in enumerate(entries, start=1):
            if entry in text:
                text = text.replace(entry, f"[MASKED-{number}]")
                fired.add(number)
        return text

    for message in result.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = scrub(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = scrub(part["text"])
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                function["arguments"] = scrub(function["arguments"])
    return result, sorted(fired)
