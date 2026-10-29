"""The flight log and Steward's small state tables, in one SQLite file (ADR 0004, Phase 1 D1).

events is append-only (triggers abort any UPDATE or DELETE) and hash-chained: each row stores
the hash of the previous row and its own hash over the canonical JSON of
{seq, ts, session_id, agent, door, kind, payload, prev_hash}. The payload column stores
jcs(payload), so parsing it back gives the object that was hashed.

tool_pins holds the fingerprint first seen for each upstream tool. ask_counts_scoped holds the
number of agent-to-agent asks per (scope, Europe/London day, asker, contact); the scope is "live",
or the session id of a scenario run, so repeated runs of the story day start from zero.

Phase 2 adds holds (calls waiting for the owner, locked to a fingerprint), intents (execute
once: a unique idempotency key per approved call) and stand_ins (address stand-ins and what
they are bound to).

Phase 3 adds controls (an agent paused or revoked, a tool turned off for one agent, the owner's
verdict on a suspected phishing message), kept so a restart does not undo them, and job_runs
(the last scheduled time each job ran, per counter scope, so a restart never runs a job twice).

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
CREATE TABLE IF NOT EXISTS ask_counts_scoped (
    scope TEXT NOT NULL,
    day TEXT NOT NULL,
    asker TEXT NOT NULL,
    contact TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (scope, day, asker, contact)
);
CREATE TABLE IF NOT EXISTS holds (
    hold_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    tool TEXT NOT NULL,
    tool_id TEXT,
    args TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    rule TEXT NOT NULL,
    rules TEXT NOT NULL,
    reason TEXT NOT NULL,
    details TEXT NOT NULL,
    choices TEXT NOT NULL,
    state TEXT NOT NULL,
    choice TEXT,
    result TEXT,
    created TEXT NOT NULL,
    expires TEXT NOT NULL,
    decided TEXT
);
CREATE INDEX IF NOT EXISTS holds_by_state ON holds (state, created);
CREATE TABLE IF NOT EXISTS intents (
    intent_key TEXT PRIMARY KEY,
    hold_id TEXT NOT NULL,
    tool_id TEXT NOT NULL,
    state TEXT NOT NULL,
    result TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stand_ins (
    session_id TEXT NOT NULL,
    token TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (session_id, token)
);
CREATE TABLE IF NOT EXISTS controls (
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    data TEXT NOT NULL,
    since TEXT NOT NULL,
    PRIMARY KEY (agent, kind, target)
);
CREATE TABLE IF NOT EXISTS job_runs (
    job TEXT NOT NULL,
    scope TEXT NOT NULL,
    due TEXT NOT NULL,
    PRIMARY KEY (job, scope)
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


@dataclass
class Hold:
    hold_id: str
    session_id: str
    agent: str
    # tool (a held tool call), ask (an ask over a limit) or address (an address request).
    kind: str
    # The exposed tool name the agent called ("ask" for asks).
    tool: str
    tool_id: str | None
    args: dict[str, Any]
    fingerprint: str
    rule: str
    rules: list[str]
    reason: str
    details: dict[str, Any]
    choices: list[dict[str, str]]
    state: str
    created: str
    expires: str
    choice: str | None = None
    result: dict[str, Any] | None = None
    decided: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "hold_id": self.hold_id,
            "session_id": self.session_id,
            "agent": self.agent,
            "kind": self.kind,
            "tool": self.tool,
            "tool_id": self.tool_id,
            "args": self.args,
            "fingerprint": self.fingerprint,
            "rule": self.rule,
            "rules": self.rules,
            "reason": self.reason,
            "details": self.details,
            "choices": self.choices,
            "state": self.state,
            "created": self.created,
            "expires": self.expires,
            "choice": self.choice,
            "result": self.result,
            "decided": self.decided,
        }


HOLD_COLUMNS = (
    "hold_id, session_id, agent, kind, tool, tool_id, args, fingerprint, rule, rules, reason, "
    "details, choices, state, choice, result, created, expires, decided"
)


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
        session_id: str | None = None,
        since_ts: str | None = None,
    ) -> list[Event]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("agent", agent),
            ("kind", kind),
            ("door", door),
            ("session_id", session_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if after_seq is not None:
            clauses.append("seq > ?")
            params.append(after_seq)
        if since_ts is not None:
            clauses.append("ts >= ?")
            params.append(since_ts)
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

    def spend_since(
        self,
        agent: str,
        since_ts: str,
        *,
        until_ts: str | None = None,
        session_id: str | None = None,
        kind: str = "model.call",
    ) -> Decimal:
        """Sum of cost_usd over the agent's model calls in [since_ts, until_ts), optionally
        within one session (a scenario run counts only its own spend). kind picks the event
        kind (the helper's calls are logged as helper.call)."""
        query = "SELECT payload FROM events WHERE agent = ? AND kind = ? AND ts >= ?"
        params: list[Any] = [agent, kind, since_ts]
        if until_ts is not None:
            query += " AND ts < ?"
            params.append(until_ts)
        if session_id is not None:
            query += " AND session_id = ?"
            params.append(session_id)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
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

    def bump_ask_count(self, day: str, asker: str, contact: str, scope: str = "live") -> int:
        """Count one more ask on this day and return the new count."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT INTO ask_counts_scoped (scope, day, asker, contact, count) "
                    "VALUES (?, ?, ?, ?, 1) ON CONFLICT (scope, day, asker, contact) "
                    "DO UPDATE SET count = ask_counts_scoped.count + 1",
                    (scope, day, asker, contact),
                )
                row = self._conn.execute(
                    "SELECT count FROM ask_counts_scoped "
                    "WHERE scope = ? AND day = ? AND asker = ? AND contact = ?",
                    (scope, day, asker, contact),
                ).fetchone()
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return int(row[0])

    def ask_counts(self, day: str, asker: str, scope: str = "live") -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT contact, count FROM ask_counts_scoped "
                "WHERE scope = ? AND day = ? AND asker = ?",
                (scope, day, asker),
            ).fetchall()
        return {str(contact): int(count) for contact, count in rows}

    # Holds ------------------------------------------------------------------------------------

    def insert_hold(self, hold: Hold) -> None:
        row = _hold_row(hold)
        marks = ", ".join("?" for _ in row)
        with self._lock:
            self._conn.execute(f"INSERT INTO holds ({HOLD_COLUMNS}) VALUES ({marks})", row)

    def hold(self, hold_id: str) -> Hold | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {HOLD_COLUMNS} FROM holds WHERE hold_id = ?", (hold_id,)
            ).fetchone()
        return None if row is None else _hold_from_row(row)

    def holds(
        self, *, state: str | None = None, session_id: str | None = None, limit: int = 200
    ) -> list[Hold]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("state", state), ("session_id", session_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 1000)))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {HOLD_COLUMNS} FROM holds {where} ORDER BY created DESC, hold_id LIMIT ?",
                params,
            ).fetchall()
        return [_hold_from_row(row) for row in rows]

    def move_hold(
        self,
        hold_id: str,
        *,
        from_state: str,
        to_state: str,
        choice: str | None = None,
        result: dict[str, Any] | None = None,
        decided: str | None = None,
    ) -> bool:
        """Compare and set: change the state only if it is still from_state, so two decisions
        on the same hold cannot both win."""
        sets = ["state = ?"]
        params: list[Any] = [to_state]
        for column, value in (("choice", choice), ("decided", decided)):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        if result is not None:
            sets.append("result = ?")
            params.append(jcs(result).decode("utf-8"))
        params += [hold_id, from_state]
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE holds SET {', '.join(sets)} WHERE hold_id = ? AND state = ?", params
            )
        return cursor.rowcount == 1

    # Intents (execute once) -------------------------------------------------------------------

    def start_intent(self, intent_key: str, hold_id: str, tool_id: str, now: str) -> bool:
        """Claim the idempotency key. False means the call already ran or is running."""
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO intents (intent_key, hold_id, tool_id, state, result, created, "
                "updated) VALUES (?, ?, ?, 'executing', NULL, ?, ?) "
                "ON CONFLICT (intent_key) DO NOTHING",
                (intent_key, hold_id, tool_id, now, now),
            )
        return cursor.rowcount == 1

    def finish_intent(
        self, intent_key: str, state: str, result: dict[str, Any] | None, now: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE intents SET state = ?, result = ?, updated = ? WHERE intent_key = ?",
                (state, None if result is None else jcs(result).decode("utf-8"), now, intent_key),
            )

    def intent(self, intent_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT intent_key, hold_id, tool_id, state, result, created, updated "
                "FROM intents WHERE intent_key = ?",
                (intent_key,),
            ).fetchone()
        return None if row is None else _intent_from_row(row)

    def intents(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT intent_key, hold_id, tool_id, state, result, created, updated "
                "FROM intents ORDER BY created DESC LIMIT ?",
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return [_intent_from_row(row) for row in rows]

    # Stand-ins --------------------------------------------------------------------------------

    def save_stand_in(self, session_id: str, token: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO stand_ins (session_id, token, data) VALUES (?, ?, ?) "
                "ON CONFLICT (session_id, token) DO UPDATE SET data = excluded.data",
                (session_id, token, jcs(data).decode("utf-8")),
            )

    def stand_in(self, session_id: str, token: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM stand_ins WHERE session_id = ? AND token = ?",
                (session_id, token),
            ).fetchone()
        if row is None:
            return None
        data: dict[str, Any] = json.loads(row[0])
        return data

    def stand_ins(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM stand_ins WHERE session_id = ? ORDER BY token", (session_id,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    # Controls (Phase 3) -----------------------------------------------------------------------

    def set_control(
        self, agent: str, kind: str, target: str, data: dict[str, Any], since: str
    ) -> None:
        """kind: paused, revoked, tool_off (target = the exposed tool name) or mail_verdict
        (target = the message id). target is "" when the control is about the whole agent."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO controls (agent, kind, target, data, since) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (agent, kind, target) DO UPDATE SET data = excluded.data, "
                "since = excluded.since",
                (agent, kind, target, jcs(data).decode("utf-8"), since),
            )

    def clear_control(self, agent: str, kind: str, target: str = "") -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM controls WHERE agent = ? AND kind = ? AND target = ?",
                (agent, kind, target),
            )
        return cursor.rowcount == 1

    def controls(self, kind: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT agent, kind, target, data, since FROM controls"
        params: list[Any] = []
        if kind is not None:
            query += " WHERE kind = ?"
            params.append(kind)
        with self._lock:
            rows = self._conn.execute(query + " ORDER BY agent, kind, target", params).fetchall()
        return [
            {
                "agent": str(row[0]),
                "kind": str(row[1]),
                "target": str(row[2]),
                "data": json.loads(row[3]),
                "since": str(row[4]),
            }
            for row in rows
        ]

    # Scheduled jobs (Phase 3) -----------------------------------------------------------------

    def last_job_run(self, job: str, scope: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT due FROM job_runs WHERE job = ? AND scope = ?", (job, scope)
            ).fetchone()
        return None if row is None else str(row[0])

    def claim_job_run(self, job: str, scope: str, due: str) -> bool:
        """Record that the run due at `due` started. False if it (or a later one) already did,
        so one due time never runs twice, even across a restart."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT due FROM job_runs WHERE job = ? AND scope = ?", (job, scope)
                ).fetchone()
                if row is not None and str(row[0]) >= due:
                    self._conn.execute("ROLLBACK")
                    return False
                self._conn.execute(
                    "INSERT INTO job_runs (job, scope, due) VALUES (?, ?, ?) "
                    "ON CONFLICT (job, scope) DO UPDATE SET due = excluded.due",
                    (job, scope, due),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return True

    def backup_to(self, path: Path | str) -> None:
        """A consistent copy of the whole database (SQLite's online backup API)."""
        target = sqlite3.connect(str(path))
        try:
            with self._lock:
                self._conn.backup(target)
        finally:
            target.close()


def _hold_row(hold: Hold) -> tuple[Any, ...]:
    def text(value: Any) -> str:
        return jcs(value).decode("utf-8")

    return (
        hold.hold_id,
        hold.session_id,
        hold.agent,
        hold.kind,
        hold.tool,
        hold.tool_id,
        text(hold.args),
        hold.fingerprint,
        hold.rule,
        text(hold.rules),
        hold.reason,
        text(hold.details),
        text(hold.choices),
        hold.state,
        hold.choice,
        None if hold.result is None else text(hold.result),
        hold.created,
        hold.expires,
        hold.decided,
    )


def _hold_from_row(row: tuple[Any, ...]) -> Hold:
    return Hold(
        hold_id=str(row[0]),
        session_id=str(row[1]),
        agent=str(row[2]),
        kind=str(row[3]),
        tool=str(row[4]),
        tool_id=None if row[5] is None else str(row[5]),
        args=json.loads(row[6]),
        fingerprint=str(row[7]),
        rule=str(row[8]),
        rules=json.loads(row[9]),
        reason=str(row[10]),
        details=json.loads(row[11]),
        choices=json.loads(row[12]),
        state=str(row[13]),
        choice=None if row[14] is None else str(row[14]),
        result=None if row[15] is None else json.loads(row[15]),
        created=str(row[16]),
        expires=str(row[17]),
        decided=None if row[18] is None else str(row[18]),
    )


def _intent_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "intent_key": str(row[0]),
        "hold_id": str(row[1]),
        "tool_id": str(row[2]),
        "state": str(row[3]),
        "result": None if row[4] is None else json.loads(row[4]),
        "created": str(row[5]),
        "updated": str(row[6]),
    }


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
