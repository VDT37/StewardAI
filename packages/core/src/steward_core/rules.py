"""Rules for tool calls (Phase 2 Parts B and C): classes, the trifecta, stand-ins, the trip
limit and stale writes.

decide() is a pure function. The tool door gathers the facts first (the agent's flags, the
stand-in bindings, a quote and the trip total, the item's current version), then decide()
returns one verdict naming every rule that applied, so the same facts always give the same
answer and replay can check it.

The trifecta (Simon Willison's lethal trifecta, Meta's Rule of Two): once an agent has read the
owner's private data and has seen untrusted content, every call that can send data out is held
for the owner. The flags are set when a result returns, so the call that brings in untrusted
content itself still goes through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from steward_core.address import COVERED_TOOL, StandIn, find_stand_ins
from steward_core.checker import Decision
from steward_core.clock import iso_ms, last_flag_reset, parse_iso
from steward_core.policy import HOLD_CLASSES, ToolRule

# Order in which rules are named on a card: the class rule (always true for the class), then
# the trifecta, then the money and version checks.
HOLD_ORDER = (
    "send_needs_owner",
    "paid_change_needs_owner",
    "destructive_needs_owner",
    "trifecta",
    "trip_limit",
    "stale_write",
)
REASONS = {
    "send_needs_owner": "sending a message out always needs you",
    "paid_change_needs_owner": "changing a paid booking always needs you",
    "destructive_needs_owner": "a call that deletes something always needs you",
    "trifecta": (
        "this agent has read your private data and untrusted content, so anything that can "
        "send data out waits for you"
    ),
    "trip_limit": "this would take the trip over its spending limit",
    "stale_write": "the item changed since the agent read it",
}


@dataclass(frozen=True)
class CallFacts:
    agent: str
    tool_id: str
    rule: ToolRule
    arguments: dict[str, Any]
    now: datetime
    private_read: bool = False
    untrusted_seen: bool = False
    # Every stand-in token in the arguments, with its binding (None when unknown).
    stand_ins: dict[str, StandIn | None] = field(default_factory=dict)
    # spend and change_paid: the quote's price and trip date, the trip total booked so far.
    price_gbp: Decimal | None = None
    trip_date: str | None = None
    trip_total_gbp: Decimal | None = None
    trip_limit_gbp: Decimal | None = None
    # versioned writes: the item's current version (None when not read or not found).
    current_version: int | None = None


@dataclass(frozen=True)
class CallVerdict:
    decision: Decision
    rule: str
    reason: str
    rules: tuple[str, ...] = ()
    # The stand-in whose approval covers this call (the one taxi booking, P2-D3).
    covered_by: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "decision": self.decision.value,
            "rule": self.rule,
            "reason": self.reason,
            "rules": list(self.rules),
        }
        if self.covered_by:
            data["covered_by"] = self.covered_by
        return data


def _refuse(rule: str, reason: str) -> CallVerdict:
    return CallVerdict(Decision.REFUSE, rule, reason, (rule,))


def check_stand_ins(facts: CallFacts) -> tuple[CallVerdict | None, str | None]:
    """A refusal if any stand-in is misused, else the token whose approval covers the call."""
    covered: str | None = None
    used = find_stand_ins(facts.arguments)
    for path, token in used:
        argument = path.split(".")[0].split("[")[0]
        if argument not in facts.rule.stand_in_args:
            return _refuse(
                "stand_in_misuse", f"{token} may only be used in a booking's address"
            ), None
        binding = facts.stand_ins.get(token)
        if binding is None:
            return _refuse("stand_in_unknown", f"{token} was never issued in this session"), None
        if binding.agent != facts.agent or facts.tool_id not in binding.tools:
            return _refuse("stand_in_misuse", f"{token} is not valid for this call"), None
        if parse_iso(binding.expires) <= facts.now:
            return _refuse("stand_in_expired", f"{token} has expired"), None
        if facts.trip_date is not None and facts.trip_date != binding.trip_date:
            return _refuse(
                "stand_in_misuse", f"{token} is only valid for the trip on {binding.trip_date}"
            ), None
        booking_id = facts.arguments.get("booking_id")
        if booking_id is not None and booking_id not in binding.booking_ids:
            return _refuse(
                "stand_in_misuse", f"{token} is only valid for the booking it was used in"
            ), None
        if facts.tool_id == COVERED_TOOL:
            if binding.covers <= 0:
                return _refuse(
                    "stand_in_used", f"{token} was already used for its one taxi booking"
                ), None
            covered = token
    tokens = {token for _, token in used}
    for argument in facts.rule.stand_in_args:
        value = facts.arguments.get(argument)
        if value is not None and not (isinstance(value, str) and tokens and value in tokens):
            return _refuse(
                "stand_in_required",
                f"{argument} must be a stand-in such as [ADDR-1]; ask for the address first",
            ), None
    return None, covered


def decide(facts: CallFacts) -> CallVerdict:
    refusal, covered = check_stand_ins(facts)
    if refusal is not None:
        return refusal
    found: list[str] = []
    class_rule = HOLD_CLASSES.get(facts.rule.tool_class)
    if class_rule:
        found.append(class_rule)
    if facts.rule.is_egress and facts.private_read and facts.untrusted_seen and covered is None:
        found.append("trifecta")
    if (
        facts.rule.tool_class == "spend"
        and facts.trip_limit_gbp is not None
        and facts.price_gbp is not None
        and (facts.trip_total_gbp or Decimal(0)) + facts.price_gbp > facts.trip_limit_gbp
    ):
        found.append("trip_limit")
    if facts.rule.versioned is not None and facts.current_version is not None:
        expected = facts.arguments.get(facts.rule.versioned.version_arg)
        if expected != facts.current_version:
            found.append("stale_write")
    if found:
        ordered = tuple(sorted(found, key=HOLD_ORDER.index))
        reason = "; ".join(REASONS[r] for r in ordered)
        return CallVerdict(Decision.HOLD, ordered[0], reason, ordered)
    if covered is not None:
        return CallVerdict(
            Decision.ALLOW,
            "stand_in_covered",
            f"covered by the owner's approval of {covered} for this booking",
            ("stand_in_covered",),
            covered_by=covered,
        )
    return CallVerdict(Decision.ALLOW, "allowed", "allowed by the policy", ("allowed",))


# Trifecta flags ----------------------------------------------------------------------------

FLAGS = ("private_read", "untrusted_seen")


@dataclass
class AgentFlags:
    # Flag -> {source: when it was first seen}.
    seen: dict[str, dict[str, str]] = field(default_factory=lambda: {f: {} for f in FLAGS})
    since: datetime | None = None

    def has(self, flag: str) -> bool:
        return bool(self.seen[flag])

    def as_dict(self) -> dict[str, Any]:
        return {
            "private_read": sorted(self.seen["private_read"]),
            "untrusted_seen": sorted(self.seen["untrusted_seen"]),
            "since": iso_ms(self.since) if self.since else None,
        }


class FlagBook:
    """Trifecta flags per agent (P2-D2 option A): cleared by the owner or at 04:00 London.

    Clearing at 04:00 is worked out when the flags are next read, from the clock, so a replay
    that runs the same actions at the same times clears them at the same point.
    """

    def __init__(self) -> None:
        self._flags: dict[str, AgentFlags] = {}

    def read(self, agent: str, now: datetime) -> tuple[AgentFlags, bool]:
        """The agent's flags, and whether they were just cleared by the 04:00 reset."""
        flags = self._flags.setdefault(agent, AgentFlags())
        if flags.since is not None and flags.since < last_flag_reset(now) <= now:
            self._flags[agent] = AgentFlags()
            return self._flags[agent], True
        return flags, False

    def peek(self, agent: str, now: datetime) -> AgentFlags:
        """The flags as of now, without clearing anything (for the console). Only an agent's
        own call clears them, so a replay without the console sees the same moments."""
        flags = self._flags.get(agent, AgentFlags())
        if flags.since is not None and flags.since < last_flag_reset(now) <= now:
            return AgentFlags()
        return flags

    def set(self, agent: str, flag: str, source: str, now: datetime) -> bool:
        """Mark a flag from a source; True when the flag was not set before."""
        flags, _ = self.read(agent, now)
        fresh = not flags.has(flag)
        if source not in flags.seen[flag]:
            flags.seen[flag][source] = iso_ms(now)
        if flags.since is None:
            flags.since = now
        return fresh

    def clear(self, agent: str) -> None:
        self._flags[agent] = AgentFlags()

    def reset(self) -> None:
        self._flags.clear()

    def snapshot(self, now: datetime) -> dict[str, dict[str, Any]]:
        return {agent: self.peek(agent, now).as_dict() for agent in sorted(self._flags)}
