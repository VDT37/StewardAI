"""Steward's running parts in one place, for the admin API, the scenario runner and replay.

Owner actions that change what the doors decide (deciding a hold, clearing flags, suspending a
pass) and changes to the fixture world are logged with an "owner_action" or "world_action"
field, so replay can play them again at the same moment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from steward_core.clock import StewardClock
from steward_core.rules import FlagBook
from steward_core.store import Hold, Store
from steward_fixtures import World
from steward_gateway.a2a_door import AgentToAgentDoor
from steward_gateway.holds import HoldBook
from steward_gateway.model_door import ModelDoor
from steward_gateway.recorder import Recorder
from steward_gateway.settings import PassBook, Settings
from steward_gateway.tool_door import Catalogue, ToolDoor


@dataclass
class Runtime:
    settings: Settings
    passes: PassBook
    store: Store
    recorder: Recorder
    clock: StewardClock
    world: World
    catalogue: Catalogue
    tool_door: ToolDoor
    a2a: AgentToAgentDoor
    holds: HoldBook
    flags: FlagBook
    model_door: ModelDoor
    checker_name: str
    anchor: str
    web_backend: str = "fixture"
    # Filled while a scenario runs, and by the last replay.
    scenario: dict[str, Any] = field(default_factory=dict)
    last_replay: dict[str, Any] | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    async def apply_world_change(self, change_id: str) -> dict[str, Any]:
        """Apply one scripted change and run the change-notice check it may trigger."""
        now = self.clock.now()
        info = self.world.apply_change(change_id)
        self.recorder.record(
            "system",
            "world.change",
            {"world_action": {"change": change_id}, **info},
            agent=None,
            at=now,
        )
        if info["kind"] == "calendar_event_added":
            await self.a2a.check_notices(str(info["side"]))
        return info

    def reset_world(self, *, follow_clock: bool) -> None:
        """Back to the frozen fixtures, with clean flags, watches and burst windows."""
        self.world.reset(follow_clock=follow_clock)
        self.flags.reset()
        self.a2a.reset()
        self.recorder.record(
            "system",
            "world.reset",
            {"world_action": {"reset": True, "follow_clock": follow_clock}},
        )

    def clear_flags(self, agent: str, by: str = "owner") -> None:
        self.flags.clear(agent)
        self.recorder.record(
            "admin",
            "flags.cleared",
            {"reason": "cleared by the owner", "owner_action": {"action": "clear_flags", "by": by}},
            agent=agent,
        )

    def set_suspended(self, agent: str, suspended: bool) -> None:
        if suspended:
            self.passes.suspended.add(agent)
        else:
            self.passes.suspended.discard(agent)
        kind = "admin.pass_suspended" if suspended else "admin.pass_resumed"
        action = {"action": "suspend" if suspended else "resume"}
        self.recorder.record("admin", kind, {"agent": agent, "owner_action": action}, agent=agent)

    async def decide(self, hold_id: str, choice: str, fingerprint: str, by: str) -> Hold:
        return await self.holds.decide(hold_id, choice, fingerprint, by=by)


# The admin API was written against this name in Phase 1.
AdminContext = Runtime
