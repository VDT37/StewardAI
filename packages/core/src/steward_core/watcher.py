"""The watcher's arithmetic (Phase 3, P3-D7): hard limits over sliding windows, and baselines.

Hard limits are checked on every flight-log event, in order, so the same events always give
the same breach at the same event (a replay reproduces every pause). Three limits:

- a runaway loop: the same tool with the same arguments same_call_max times within
  same_call_minutes;
- a storm of refusals: refused or denied calls (tools, asks, passes) refusals_max times within
  refusals_minutes;
- too many model calls: more than model_calls_per_minute within one minute.

Windows are kept per (session, agent), so a story run never counts calls from another session.
Calls refused because the agent is already paused do not count: they are the pause working.

Baselines only report (the brief's "compared with earlier evenings"). A robust score compares
tonight with the agent's own history: z = (x - median) / (1.4826 * MAD), where MAD is the
median absolute deviation. 1.4826 makes MAD estimate the standard deviation for normal data,
and the median and MAD ignore a few odd evenings, unlike the mean and standard deviation.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from steward_core.canonical import jcs, sha256_hex
from steward_core.clock import parse_iso
from steward_core.policy import WatcherSettings

CALL_KINDS = frozenset(
    {
        "tool.call",
        "tool.held",
        "tool.refused",
        "tool.denied",
        "hold.status",
        "a2a.request",
        "a2a.refused",
        "a2a.held",
    }
)
REFUSAL_KINDS = frozenset({"tool.refused", "tool.denied", "a2a.refused", "auth.denied"})
# Denials that are the pause itself working: never counted towards a new breach.
PAUSE_REASONS = frozenset({"pass suspended", "pass revoked", "suspended", "revoked"})


@dataclass(frozen=True)
class Breach:
    agent: str
    rule: str
    count: int
    minutes: float
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "count": self.count,
            "minutes": self.minutes,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Observed:
    """The parts of a flight-log event the hard limits read."""

    session_id: str
    agent: str
    kind: str
    at: datetime
    payload: dict[str, Any]


class HardLimits:
    def __init__(self, settings: WatcherSettings) -> None:
        self.settings = settings
        self._windows: dict[tuple[str, str, str], deque[datetime]] = {}

    def reset(self) -> None:
        self._windows.clear()

    def _push(self, key: tuple[str, str, str], at: datetime, minutes: float) -> int:
        window = self._windows.setdefault(key, deque())
        window.append(at)
        horizon = at - timedelta(minutes=minutes)
        while window and window[0] <= horizon:
            window.popleft()
        return len(window)

    def observe(self, event: Observed) -> Breach | None:
        """Count one event; return the first limit it crosses, if any."""
        s = self.settings
        base = (event.session_id, event.agent)
        if event.kind in CALL_KINDS and isinstance(event.payload.get("call"), dict):
            call = event.payload["call"]
            key = (*base, "same:" + sha256_hex(jcs(call))[:16])
            count = self._push(key, event.at, s.same_call_minutes)
            if count >= s.same_call_max:
                return Breach(
                    event.agent,
                    "runaway_loop",
                    count,
                    s.same_call_minutes,
                    f"{call.get('tool')} with the same arguments {count} times in "
                    f"{_minutes(s.same_call_minutes)}",
                )
        if event.kind in REFUSAL_KINDS and not _pause_denial(event.payload):
            count = self._push((*base, "refusals"), event.at, s.refusals_minutes)
            if count >= s.refusals_max:
                return Breach(
                    event.agent,
                    "refusal_storm",
                    count,
                    s.refusals_minutes,
                    f"{count} refused or denied calls in {_minutes(s.refusals_minutes)}",
                )
        if event.kind == "model.call":
            count = self._push((*base, "model"), event.at, 1)
            if count > s.model_calls_per_minute:
                return Breach(
                    event.agent,
                    "model_call_rate",
                    count,
                    1,
                    f"{count} model calls in one minute",
                )
        return None


def observed(event: Any) -> Observed | None:
    """An Observed from a store Event (or None when the event has no agent)."""
    agent = getattr(event, "agent", None)
    if not agent:
        return None
    return Observed(
        session_id=str(event.session_id),
        agent=str(agent),
        kind=str(event.kind),
        at=parse_iso(str(event.ts)),
        payload=dict(event.payload),
    )


def _pause_denial(payload: dict[str, Any]) -> bool:
    reason = str(payload.get("reason") or "")
    return reason in PAUSE_REASONS


def _minutes(value: float) -> str:
    whole = int(value) if float(value).is_integer() else value
    return f"{whole} minute" + ("" if whole == 1 else "s")


@dataclass(frozen=True)
class Baseline:
    """Tonight's value against the agent's own history."""

    metric: str
    value: float
    history: int
    median: float | None
    mad: float | None
    z: float | None
    verdict: str  # learning (too little history), usual or unusual

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "history": self.history,
            "median": self.median,
            "mad": self.mad,
            "z": None if self.z is None else round(self.z, 2),
            "verdict": self.verdict,
        }


MIN_HISTORY = 3
UNUSUAL_Z = 3.5
# A rise must also be this much in absolute terms, so 2 against a usual 0 is not "unusual".
MIN_RISE = 3.0


def baseline(metric: str, value: float, history: Sequence[float]) -> Baseline:
    """Robust z-score of value against history (median and MAD, scaled by 1.4826)."""
    if len(history) < MIN_HISTORY:
        return Baseline(metric, value, len(history), None, None, None, "learning")
    mid = float(median(history))
    mad = float(median([abs(h - mid) for h in history]))
    spread = 1.4826 * mad
    rise = value - mid
    if spread == 0:
        # Every earlier evening was the same, so there is no spread to divide by (z stays
        # empty): a clear rise alone counts as unusual.
        z = None
        unusual = rise >= MIN_RISE
    else:
        z = rise / spread
        unusual = z >= UNUSUAL_Z and rise >= MIN_RISE
    return Baseline(metric, value, len(history), mid, mad, z, "unusual" if unusual else "usual")
