"""Steward's running parts in one place, for the admin API, the scenario runner and replay.

Owner actions that change what the doors decide (deciding a hold, clearing flags, suspending a
pass) and changes to the fixture world are logged with an "owner_action" or "world_action"
field, so replay can play them again at the same moment.

Phase 3 adds the contain controls (pause, resume, revoke, a tool turned off for one agent, the
owner's verdict on a suspected phishing message). Each is kept in the controls table, so a
restart never lifts it, and each is an owner action, so replay plays it again. A pause by the
watcher is not an owner action: replay reproduces it from the same events.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from steward_core.clock import StewardClock, iso_ms
from steward_core.rules import FlagBook
from steward_core.store import Hold, Store
from steward_fixtures import World
from steward_gateway.a2a_door import AgentToAgentDoor
from steward_gateway.holds import HoldBook
from steward_gateway.model_door import ModelDoor
from steward_gateway.recorder import Recorder
from steward_gateway.settings import PassBook, Settings
from steward_gateway.tool_door import Catalogue, ToolDoor

if TYPE_CHECKING:
    from steward_gateway.supervisor import Briefing, Scheduler, Watcher


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
    # Phase 3: the supervisor's parts (None when the policy leaves them out).
    scheduler: Scheduler | None = None
    briefing: Briefing | None = None
    watcher: Watcher | None = None
    # "owner" (the real Steward) or "demo" (the public, read-only demo, Part F).
    mode: str = "owner"

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

    def set_suspended(self, agent: str, suspended: bool, by: str = "owner") -> None:
        """The owner's pause (suspend) and resume of one agent's pass, at every door."""
        if not suspended and agent in self.passes.revoked:
            raise ValueError("the pass is revoked; issue a new pass to lift it")
        now = self.clock.now()
        if suspended:
            self.passes.suspended.add(agent)
            self.store.set_control(agent, "paused", "", {"by": by}, iso_ms(now))
        else:
            self.passes.suspended.discard(agent)
            self.store.clear_control(agent, "paused")
        kind = "admin.pass_suspended" if suspended else "admin.pass_resumed"
        action = {"action": "suspend" if suspended else "resume"}
        self.recorder.record(
            "admin", kind, {"agent": agent, "owner_action": action}, agent=agent, at=now
        )
        if suspended:
            self.stop_runs(agent)

    # Contain (Phase 3) ------------------------------------------------------------------------

    def load_controls(self) -> None:
        """Apply the saved controls at start: a restart never lifts a pause, a revocation or a
        tool switch. A revocation ends once the agent has a new pass."""
        for control in self.store.controls():
            agent, kind, target = control["agent"], control["kind"], control["target"]
            if kind in {"paused", "revoked"} and agent in self.settings.policy.agents:
                if kind == "revoked" and control["data"].get("pass") != self.passes.pass_hash(
                    agent
                ):
                    self.store.clear_control(agent, "revoked")
                    self.recorder.record(
                        "admin",
                        "admin.revocation_lifted",
                        {"agent": agent, "reason": "a new pass is in use"},
                        agent=agent,
                    )
                    continue
                self.passes.suspended.add(agent)
                if kind == "revoked":
                    self.passes.revoked.add(agent)
            elif kind == "tool_off" and agent in self.tool_door.disabled:
                self.tool_door.disabled[agent].add(target)
            elif kind == "mail_verdict" and control["data"].get("verdict") == "not_phishing":
                self.tool_door.cleared_mail.add(target)

    def watcher_pause(self, agent: str, breach: dict[str, Any], at: datetime) -> Hold | None:
        """The watcher's pause at a hard limit, with a card to resume. Not an owner action:
        replay reaches the same pause from the same events."""
        if agent in self.passes.suspended:
            return None
        self.passes.suspended.add(agent)
        self.store.set_control(agent, "paused", "", {"by": "watcher", **breach}, iso_ms(at))
        detail = str(breach.get("detail") or breach.get("rule"))
        hold = self.holds.create(
            agent=agent,
            kind="pause",
            tool="pause",
            tool_id=None,
            args={"agent": agent, "rule": breach.get("rule")},
            rule=str(breach.get("rule")),
            rules=[str(breach.get("rule"))],
            reason=f"the watcher paused the {agent} agent: {detail}",
            details={
                "card": f"The watcher paused the {agent} agent ({detail}). Resume it?",
                "breach": breach,
            },
            now=at,
        )
        self.recorder.record(
            "admin",
            "watcher.paused",
            {
                "agent": agent,
                **breach,
                "hold_id": hold.hold_id,
                "fingerprint": hold.fingerprint,
            },
            agent=agent,
            at=at,
        )
        self.stop_runs(agent)
        return hold

    async def resume_from_card(self, hold: Hold, choice: str) -> tuple[str, dict[str, Any]]:
        """Executor for a watcher pause card: "Resume" lifts the pause (the decision itself is
        the owner action, so this event carries none)."""
        agent = str(hold.args.get("agent"))
        if agent in self.passes.revoked:
            return "failed", {"error": "the pass is revoked"}
        self.passes.suspended.discard(agent)
        self.store.clear_control(agent, "paused")
        self.recorder.record(
            "admin",
            "admin.pass_resumed",
            {"agent": agent, "hold_id": hold.hold_id, "by": "owner (card)"},
            agent=agent,
        )
        return "done", {"resumed": agent}

    def revoke(self, agent: str, by: str = "owner") -> None:
        """Pause for good: only a new pass for the agent lifts it."""
        now = self.clock.now()
        self.passes.suspended.add(agent)
        self.passes.revoked.add(agent)
        self.store.set_control(
            agent, "revoked", "", {"by": by, "pass": self.passes.pass_hash(agent)}, iso_ms(now)
        )
        self.recorder.record(
            "admin",
            "admin.pass_revoked",
            {"agent": agent, "owner_action": {"action": "revoke", "by": by}},
            agent=agent,
            at=now,
        )
        self.stop_runs(agent)

    def set_tool(self, agent: str, tool: str, enabled: bool, by: str = "owner") -> None:
        """Turn one of an agent's tools (exposed name) off or back on."""
        if tool not in self.tool_door.exposures.get(agent, {}):
            raise ValueError(f"{tool!r} is not on the {agent} agent's pass")
        now = self.clock.now()
        if enabled:
            self.tool_door.disabled[agent].discard(tool)
            self.store.clear_control(agent, "tool_off", tool)
        else:
            self.tool_door.disabled[agent].add(tool)
            self.store.set_control(agent, "tool_off", tool, {"by": by}, iso_ms(now))
        action = {"action": "tool_on" if enabled else "tool_off", "tool": tool, "by": by}
        self.recorder.record(
            "admin",
            "admin.tool_on" if enabled else "admin.tool_off",
            {"agent": agent, "tool": tool, "owner_action": action},
            agent=agent,
            at=now,
        )

    def mail_verdict(self, message_id: str, verdict: str, by: str = "owner") -> None:
        """The owner's verdict on a suspected phishing message. "not_phishing" lets its links
        through on the next read; "phishing" keeps them withheld."""
        if verdict not in {"phishing", "not_phishing"}:
            raise ValueError("verdict must be phishing or not_phishing")
        now = self.clock.now()
        self.store.set_control(
            "owner", "mail_verdict", message_id, {"verdict": verdict, "by": by}, iso_ms(now)
        )
        if verdict == "not_phishing":
            self.tool_door.cleared_mail.add(message_id)
        else:
            self.tool_door.cleared_mail.discard(message_id)
        action = {"action": "mail_verdict", "message_id": message_id, "verdict": verdict}
        self.recorder.record(
            "admin",
            "mail.verdict",
            {"message_id": message_id, "verdict": verdict, "owner_action": {**action, "by": by}},
            at=now,
        )

    def stop_runs(self, agent: str) -> None:
        """Stop the agent's run in progress, if its adapter can (Hermes: POST .../stop)."""
        adapter = self.a2a.adapters.get(agent)
        stop = getattr(adapter, "stop", None)
        if stop is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def run() -> None:
            stopped = await stop()
            if stopped:
                self.recorder.record("admin", "admin.run_stopped", {"run_id": stopped}, agent=agent)

        task = loop.create_task(run())
        self.extras.setdefault("tasks", set()).add(task)
        task.add_done_callback(self.extras["tasks"].discard)

    async def decide(self, hold_id: str, choice: str, fingerprint: str, by: str) -> Hold:
        return await self.holds.decide(hold_id, choice, fingerprint, by=by)


# The admin API was written against this name in Phase 1.
AdminContext = Runtime
