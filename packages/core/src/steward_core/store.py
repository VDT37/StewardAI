"""The flight log and Steward's small state tables, in one SQLite file (ADR 0004, Phase 1 D1).

events is append-only (triggers abort any UPDATE or DELETE) and hash-chained: each row stores
the hash of the previous row and its own hash over the canonical JSON of
{seq, ts, session_id, agent, door, kind, payload, prev_hash}. The payload column stores
jcs(payload), so parsing it back gives the object that was hashed.

tool_pins holds the fingerprint first seen for each upstream tool. ask_counts holds the number
of agent-to-agent asks per (Europe/London day, asker, contact).

One connection, one writer lock: appends read the last hash and insert inside one
BEGIN IMMEDIATE transaction, so the chain cannot fork. The SQL avoids SQLite-only types so the
same statements work on PostgreSQL if the store is ever swapped.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from steward_core.canonical import GENESIS, chain_hash, jcs
from steward_core.clock import iso_ms, utc_now

Door = Literal["model", "tool", "a2a", "auth", "admin", "system"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    session_id TEXT NOT NULL,
    agent TEXT,
    door TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_agent ON events (agent, kind, ts);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'flight log is append-only');
END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'flight log is append-only');
END;
CREATE TABLE IF NOT EXISTS tool_pins (
    tool_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    pinned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ask_counts (
    day TEXT NOT NULL,
    asker TEXT NOT NULL,
    contact TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (day, asker, contact)
);
"""

COLUMNS = "seq, ts, session_id, agent, door, kind, payload, prev_hash, hash"


@dataclass(frozen=True)
class Event:
    seq: int
    ts: str
    session_id: str
    agent: str | None
    door: str
    kind: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    def hashed_row(self) -> dict[str, Any]:
        """The object the row's hash covers: every column except the hash itself."""
        return {
            "seq": self.seq,
            "ts": self.ts,
            "session_id": self.session_id,
            "agent": self.agent,
            "door": self.door,
            "kind": self.kind,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
        }

    def as_dict(self) -> dict[str, Any]:
        return {**self.hashed_row(), "hash": self.hash}


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    rows: int
    head_hash: str
    first_bad_seq: int | None = None
    reason: str | None = None


Listener = Callable[[Event], None]


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit mode (isolation_level=None): transactions are opened explicitly below.
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # Flight log -------------------------------------------------------------------------------

    def subscribe(self, listener: Listener) -> None:
        """Call listener with every event appended from now on (the live log uses this)."""
        self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def append(
        self,
        *,
        session_id: str,
        agent: str | None,
        door: Door,
        kind: str,
        payload: Mapping[str, Any],
        at: datetime | None = None,
    ) -> Event:
        """Append one event and return it with its seq and hashes."""
        payload_text = jcs(dict(payload)).decode("utf-8")
        payload_obj: dict[str, Any] = json.loads(payload_text)
        ts = iso_ms(at or utc_now())
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                last = self._conn.execute(
                    "SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                seq = 1 if last is None else int(last[0]) + 1
                prev_hash = GENESIS if last is None else str(last[1])
                event = Event(seq, ts, session_id, agent, door, kind, payload_obj, prev_hash, "")
                digest = chain_hash(prev_hash, event.hashed_row())
                self._conn.execute(
                    f"INSERT INTO events ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (seq, ts, session_id, agent, door, kind, payload_text, prev_hash, digest),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        event = Event(seq, ts, session_id, agent, door, kind, payload_obj, prev_hash, digest)
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                # A broken live-log subscriber must never stop the log itself.
                self.unsubscribe(listener)
        return event

    def events(
        self,
        *,
        agent: str | None = None,
        kind: str | None = None,
        door: str | None = None,
        after_seq: int | None = None,
        limit: int = 100,
        newest_first: bool = True,
    ) -> list[Event]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("agent", agent), ("kind", kind), ("door", door)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if after_seq is not None:
            clauses.append("seq > ?")
            params.append(after_seq)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "DESC" if newest_first else "ASC"
        params.append(max(1, min(limit, 5000)))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {COLUMNS} FROM events {where} ORDER BY seq {order} LIMIT ?", params
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def head(self) -> tuple[int, str]:
        """(last seq, last hash), or (0, GENESIS) for an empty log."""
        with self._lock:
            last = self._conn.execute(
                "SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        return (0, GENESIS) if last is None else (int(last[0]), str(last[1]))

    def verify(self) -> VerifyResult:
        """Recompute every hash from genesis; report the first row that does not match."""
        prev_hash = GENESIS
        expected = 1
        with self._lock:
            rows = self._conn.execute(f"SELECT {COLUMNS} FROM events ORDER BY seq").fetchall()
        for row in rows:
            seq = int(row[0])
            if seq != expected:
                return VerifyResult(False, expected - 1, prev_hash, seq, "a row is missing")
            if row[7] != prev_hash:
                return VerifyResult(
                    False, expected - 1, prev_hash, seq, "prev_hash does not match the row before"
                )
            try:
                event = _event_from_row(row)
                digest = chain_hash(prev_hash, event.hashed_row())
            except (ValueError, TypeError) as exc:
                return VerifyResult(
                    False, expected - 1, prev_hash, seq, f"row cannot be read: {exc}"
                )
            if digest != row[8]:
                return VerifyResult(
                    False, expected - 1, prev_hash, seq, "hash does not match the row"
                )
            prev_hash = digest
            expected += 1
        return VerifyResult(True, expected - 1, prev_hash)

    def export_lines(self) -> Iterator[str]:
        """One canonical JSON object per event, oldest first (the format replay reads)."""
        with self._lock:
            rows = self._conn.execute(f"SELECT {COLUMNS} FROM events ORDER BY seq").fetchall()
        for row in rows:
            yield jcs(_event_from_row(row).as_dict()).decode("utf-8")

    def spend_since(self, agent: str, since_ts: str) -> Decimal:
        """Sum of cost_usd over the agent's model calls at or after since_ts."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM events WHERE agent = ? AND kind = 'model.call' AND ts >= ?",
                (agent, since_ts),
            ).fetchall()
        total = Decimal(0)
        for (text,) in rows:
            cost = json.loads(text).get("cost_usd")
            if isinstance(cost, str):
                try:
                    total += Decimal(cost)
                except InvalidOperation:
                    continue
        return total

    # Tool pins --------------------------------------------------------------------------------

    def pin(self, tool_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT fingerprint FROM tool_pins WHERE tool_id = ?", (tool_id,)
            ).fetchone()
        return None if row is None else str(row[0])

    def set_pin(self, tool_id: str, fingerprint: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tool_pins (tool_id, fingerprint, pinned_at) VALUES (?, ?, ?) "
                "ON CONFLICT (tool_id) DO UPDATE SET fingerprint = excluded.fingerprint, "
                "pinned_at = excluded.pinned_at",
                (tool_id, fingerprint, iso_ms(utc_now())),
            )

    # Ask counts -------------------------------------------------------------------------------

    def bump_ask_count(self, day: str, asker: str, contact: str) -> int:
        """Count one more ask on this day and return the new count."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT INTO ask_counts (day, asker, contact, count) VALUES (?, ?, ?, 1) "
                    "ON CONFLICT (day, asker, contact) DO UPDATE SET count = ask_counts.count + 1",
                    (day, asker, contact),
                )
                row = self._conn.execute(
                    "SELECT count FROM ask_counts WHERE day = ? AND asker = ? AND contact = ?",
                    (day, asker, contact),
                ).fetchone()
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return int(row[0])

    def ask_counts(self, day: str, asker: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT contact, count FROM ask_counts WHERE day = ? AND asker = ?", (day, asker)
            ).fetchall()
        return {str(contact): int(count) for contact, count in rows}


def _event_from_row(row: tuple[Any, ...]) -> Event:
    payload = json.loads(row[6])
    if not isinstance(payload, dict):
        raise ValueError("payload is not a JSON object")
    return Event(
        seq=int(row[0]),
        ts=str(row[1]),
        session_id=str(row[2]),
        agent=None if row[3] is None else str(row[3]),
        door=str(row[4]),
        kind=str(row[5]),
        payload=payload,
        prev_hash=str(row[7]),
        hash=str(row[8]),
    )
