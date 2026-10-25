"""Holds, approvals and execute-once: Steward's action engine (Phase 2 Part B).

A held call is saved with its exact payload and a fingerprint,
sha256(jcs({"agent", "tool", "args", "hold_id"})). The owner approves by naming the hold and its
fingerprint; any other fingerprint voids the approval, so what the owner saw is what runs.
Approving runs the call once (an executor per hold kind does the work): for a tool call an
intent row first claims a unique idempotency key, sha256(jcs({"hold_id", "tool", "args"})), so a
second approval cannot run it again, and a timeout or error leaves the intent "unknown" for the
owner instead of being retried.

Holds expire (policy holds.expires_hours, on Steward's clock). Expiry is worked out whenever a
hold is read, so a replay at the same times expires the same holds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from steward_core.canonical import jcs, sha256_hex
from steward_core.clock import Clock, iso_ms, parse_iso
from steward_core.policy import Policy
from steward_core.store import Hold, Store
from steward_gateway.recorder import Recorder

CHOICES: dict[str, list[dict[str, str]]] = {
    "tool": [{"id": "approve", "label": "Allow once"}, {"id": "deny", "label": "Deny"}],
    "stale": [
        {"id": "approve", "label": "Use this write"},
        {"id": "deny", "label": "Keep current version"},
    ],
    "address": [{"id": "taxi_only", "label": "Taxi booking only"}, {"id": "deny", "label": "Deny"}],
    "ask": [{"id": "approve", "label": "Send this ask"}, {"id": "deny", "label": "Deny"}],
}
DENY = frozenset({"deny"})
# Hold states: pending -> approved -> done | failed | unknown, or pending -> denied | expired.
FINAL = frozenset({"done", "failed", "unknown", "denied", "expired"})

# An executor runs an approved hold and returns (final state, result for the agent).
Executor = Callable[[Hold, str], Awaitable[tuple[str, dict[str, Any]]]]
Listener = Callable[[Hold, str], None]


class HoldError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def hold_fingerprint(agent: str, tool: str, args: Any, hold_id: str) -> str:
    return sha256_hex(jcs({"agent": agent, "tool": tool, "args": args, "hold_id": hold_id}))


def intent_key(hold_id: str, tool: str, args: Any) -> str:
    return sha256_hex(jcs({"hold_id": hold_id, "tool": tool, "args": args}))


def short_fingerprint(fingerprint: str) -> str:
    return f"{fingerprint[:4]}...{fingerprint[-3:]}"


class HoldBook:
    def __init__(self, store: Store, recorder: Recorder, policy: Policy, clock: Clock) -> None:
        self.store = store
        self.recorder = recorder
        self.policy = policy
        self.clock = clock
        self.executors: dict[str, Executor] = {}
        # Kinds whose executor may take long (an ask waits for the other agent): it runs in
        # the background and the decision returns at once.
        self.background: set[str] = {"ask"}
        self.listeners: list[Listener] = []
        self._tasks: set[asyncio.Task[None]] = set()

    def _notify(self, hold: Hold, event: str) -> None:
        for listener in list(self.listeners):
            try:
                listener(hold, event)
            except Exception:
                # A broken notifier (Telegram down) must never block a decision.
                continue

    def create(
        self,
        *,
        agent: str,
        kind: str,
        tool: str,
        tool_id: str | None,
        args: dict[str, Any],
        rule: str,
        rules: list[str],
        reason: str,
        details: dict[str, Any],
        now: datetime,
        choices: str | None = None,
    ) -> Hold:
        hold_id = self.recorder.ids.next("h")
        hold = Hold(
            hold_id=hold_id,
            session_id=self.recorder.session_id,
            agent=agent,
            kind=kind,
            tool=tool,
            tool_id=tool_id,
            args=args,
            fingerprint=hold_fingerprint(agent, tool, args, hold_id),
            rule=rule,
            rules=rules,
            reason=reason,
            details=details,
            choices=CHOICES[choices or kind],
            state="pending",
            created=iso_ms(now),
            expires=iso_ms(now + timedelta(hours=self.policy.holds.expires_hours)),
        )
        self.store.insert_hold(hold)
        self._notify(hold, "created")
        return hold

    def get(self, hold_id: str, now: datetime | None = None) -> Hold | None:
        hold = self.store.hold(hold_id)
        if hold is None:
            return None
        return self._expire_if_due(hold, now or self.clock.now())

    def _expire_if_due(self, hold: Hold, now: datetime) -> Hold:
        if hold.state == "pending" and parse_iso(hold.expires) <= now:
            if self.store.move_hold(
                hold.hold_id, from_state="pending", to_state="expired", decided=iso_ms(now)
            ):
                self.recorder.record(
                    "admin",
                    "hold.expired",
                    {"hold_id": hold.hold_id, "rule": hold.rule},
                    agent=hold.agent,
                    at=now,
                )
                hold = self.store.hold(hold.hold_id) or hold
                self._notify(hold, "expired")
        return hold

    def pending(self, now: datetime | None = None) -> list[Hold]:
        moment = now or self.clock.now()
        holds = [self._expire_if_due(h, moment) for h in self.store.holds(state="pending")]
        return [h for h in holds if h.state == "pending"]

    async def decide(
        self, hold_id: str, choice: str, fingerprint: str, *, by: str = "owner"
    ) -> Hold:
        now = self.clock.now()
        action = {
            "action": "decide",
            "hold_id": hold_id,
            "choice": choice,
            "fingerprint": fingerprint,
            "by": by,
        }
        hold = self.get(hold_id, now)
        if hold is None:
            raise HoldError(404, "no such hold")
        if hold.state == "expired":
            raise HoldError(410, "the hold expired; nothing was approved")
        if hold.state != "pending":
            raise HoldError(409, f"the hold is already {hold.state}")
        if fingerprint != hold.fingerprint:
            self.recorder.record(
                "admin",
                "hold.rejected",
                {**_brief(hold), "owner_action": action, "reason": "fingerprint mismatch"},
                agent=hold.agent,
                at=now,
            )
            raise HoldError(
                409, "the fingerprint does not match the held call; nothing was approved"
            )
        if choice not in {c["id"] for c in hold.choices}:
            raise HoldError(400, f"choice must be one of {[c['id'] for c in hold.choices]}")
        if choice in DENY:
            if not self.store.move_hold(
                hold_id, from_state="pending", to_state="denied", choice=choice, decided=iso_ms(now)
            ):
                raise HoldError(409, "the hold was decided at the same moment")
            self.recorder.record(
                "admin",
                "hold.denied",
                {**_brief(hold), "owner_action": action},
                agent=hold.agent,
                at=now,
            )
            denied = self.store.hold(hold_id) or hold
            self._notify(denied, "decided")
            return denied
        if not self.store.move_hold(
            hold_id, from_state="pending", to_state="approved", choice=choice, decided=iso_ms(now)
        ):
            raise HoldError(409, "the hold was decided at the same moment")
        self.recorder.record(
            "admin",
            "hold.approved",
            {**_brief(hold), "owner_action": action},
            agent=hold.agent,
            at=now,
        )
        approved = self.store.hold(hold_id) or hold
        if hold.kind in self.background:
            task = asyncio.create_task(self._execute(approved, choice))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return approved
        await self._execute(approved, choice)
        return self.store.hold(hold_id) or approved

    async def _execute(self, hold: Hold, choice: str) -> None:
        executor = self.executors.get(hold.kind)
        if executor is None:
            state, result = "failed", {"error": f"no executor for {hold.kind} holds"}
        else:
            try:
                state, result = await executor(hold, choice)
            except Exception as exc:
                state, result = "unknown", {"error": type(exc).__name__}
        self.store.move_hold(hold.hold_id, from_state="approved", to_state=state, result=result)
        finished = self.store.hold(hold.hold_id) or hold
        self._notify(finished, "decided")

    def status(self, hold_id: str, agent: str, now: datetime | None = None) -> dict[str, Any]:
        """What the hold_status tool tells the agent that made the call."""
        hold = self.get(hold_id, now)
        if hold is None or hold.agent != agent:
            return {"hold_id": hold_id, "status": "unknown", "message": "no such hold for you"}
        data: dict[str, Any] = {"hold_id": hold_id, "status": hold.state, "tool": hold.tool}
        if hold.state == "pending":
            data["message"] = "Still waiting for the owner. Do not retry the call."
        elif hold.state == "approved":
            data["message"] = "Approved; Steward is running it now."
        elif hold.state == "done":
            data["result"] = hold.result
            data["message"] = "Approved and done once by Steward. Do not repeat the call."
        elif hold.state in {"failed", "unknown"}:
            data["result"] = hold.result
            data["message"] = "Approved, but the outcome needs the owner. Do not retry."
        elif hold.state == "denied":
            data["message"] = "The owner said no. Do not retry."
        elif hold.state == "expired":
            data["message"] = "Nobody decided in time; nothing was done."
        return data


def _brief(hold: Hold) -> dict[str, Any]:
    return {
        "hold_id": hold.hold_id,
        "kind": hold.kind,
        "tool": hold.tool,
        "rule": hold.rule,
        "fingerprint": hold.fingerprint,
    }
