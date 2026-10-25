"""The trap suite and the normal tasks (Phase 2 Part G). Run with: uv run pytest evals"""

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from trap_runner import load, run_spec

TRAPS = load("traps")
TASKS = load("tasks")
CATEGORIES = {
    "Reasons and free text": 5,
    "Planted details in replies": 6,
    "Limits and addressing": 4,
    "Address": 4,
    "Trifecta": 5,
    "Tools": 5,
    "Writes": 5,
    "Budget": 3,
    "Bypass": 3,
}


def test_the_suite_has_forty_traps_and_ten_tasks() -> None:
    assert Counter(t["category"] for t in TRAPS) == CATEGORIES
    assert len(TRAPS) == 40 and len(TASKS) == 10
    assert len({t["id"] for t in TRAPS + TASKS}) == 50


@pytest.mark.parametrize("spec", [t for t in TRAPS if not t.get("vm_only")], ids=lambda s: s["id"])
def test_trap_is_refused_held_or_dropped(spec: dict[str, Any], tmp_path: Path) -> None:
    asyncio.run(run_spec(spec, tmp_path))


@pytest.mark.parametrize("spec", TASKS, ids=lambda s: s["id"])
def test_normal_task_still_finishes(spec: dict[str, Any], tmp_path: Path) -> None:
    asyncio.run(run_spec(spec, tmp_path))
