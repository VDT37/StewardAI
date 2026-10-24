"""Writes events into the flight log under the current session."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any

from steward_core.clock import utc_now
from steward_core.store import Door, Event, Store


def new_session_id() -> str:
    return f"s-{utc_now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


class Recorder:
    def __init__(self, store: Store, session_id: str | None = None) -> None:
        self.store = store
        self.session_id = session_id or new_session_id()

    def record(
        self, door: Door, kind: str, payload: Mapping[str, Any], *, agent: str | None = None
    ) -> Event:
        """Append one event. Errors propagate: a call that cannot be logged does not go ahead."""
        return self.store.append(
            session_id=self.session_id, agent=agent, door=door, kind=kind, payload=payload
        )

    def new_session(self, label: str | None = None) -> str:
        previous = self.session_id
        self.session_id = new_session_id()
        self.record("system", "system.session", {"previous": previous, "label": label})
        return self.session_id
