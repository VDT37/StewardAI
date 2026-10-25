"""Writes events into the flight log under the current session, on Steward's clock.

The recorder also owns the session's id mint (deterministic ids, see steward_core.ids) and the
counter scope: "live" for ordinary sessions, the session id for a scenario run, so the story's
daily counters start from zero on every run and in its replay.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from steward_core.clock import Clock, StewardClock, utc_now
from steward_core.ids import IdMint
from steward_core.store import Door, Event, Store


def new_session_id() -> str:
    return f"s-{utc_now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


class Recorder:
    def __init__(
        self,
        store: Store,
        session_id: str | None = None,
        *,
        clock: Clock | None = None,
        scope: str = "live",
    ) -> None:
        self.store = store
        self.clock: Clock = clock or StewardClock()
        self.session_id = session_id or new_session_id()
        self.label: str | None = None
        self.scope = scope
        self.ids = IdMint(self.session_id)

    def record(
        self,
        door: Door,
        kind: str,
        payload: Mapping[str, Any],
        *,
        agent: str | None = None,
        at: datetime | None = None,
        session_id: str | None = None,
    ) -> Event:
        """Append one event. Errors propagate: a call that cannot be logged does not go ahead.
        session_id files the event under another session (a hold from an earlier session
        expiring now belongs to the session that created it)."""
        return self.store.append(
            session_id=session_id or self.session_id,
            agent=agent,
            door=door,
            kind=kind,
            payload=payload,
            at=at or self.clock.now(),
        )

    def new_session(
        self, label: str | None = None, *, scoped: bool = False, session_id: str | None = None
    ) -> str:
        """Start a new session. scoped=True gives it its own daily counters (scenario runs)."""
        previous = self.session_id
        self.session_id = session_id or new_session_id()
        self.label = label
        self.scope = self.session_id if scoped else "live"
        self.ids = IdMint(self.session_id)
        self.record(
            "system",
            "system.session",
            {"previous": previous, "label": label, "scope": "session" if scoped else "live"},
        )
        return self.session_id
