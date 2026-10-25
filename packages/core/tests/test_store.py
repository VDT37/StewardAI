import json
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from steward_core.canonical import GENESIS, chain_hash, jcs, tool_fingerprint
from steward_core.store import Event, Store


def fill(store: Store, n: int = 3) -> list[Event]:
    return [
        store.append(
            session_id="s-test",
            agent="work",
            door="a2a",
            kind="a2a.request",
            payload={"n": i, "note": f"tamper-me-{i:04d}"},
        )
        for i in range(n)
    ]


def test_jcs_is_canonical() -> None:
    assert jcs({"b": 1, "a": 2.0}) == b'{"a":2,"b":1}'
    assert jcs({"z": "é", "a": [True, None]}) == '{"a":[true,null],"z":"é"}'.encode()


def test_chain_links_rows(tmp_path: Path) -> None:
    store = Store(tmp_path / "log.db")
    first, second, _ = fill(store)
    assert first.seq == 1 and first.prev_hash == GENESIS
    assert second.prev_hash == first.hash
    assert second.hash == chain_hash(first.hash, second.hashed_row())
    assert store.verify().ok and store.verify().rows == 3
    assert store.head() == (3, store.events(limit=1)[0].hash)


def test_payload_round_trips_as_hashed(tmp_path: Path) -> None:
    store = Store(tmp_path / "log.db")
    event = store.append(
        session_id="s", agent=None, door="system", kind="system.start", payload={"x": 1.0}
    )
    (stored,) = store.events()
    assert stored == event and stored.payload == {"x": 1}


def test_triggers_block_update_and_delete(tmp_path: Path) -> None:
    store = Store(tmp_path / "log.db")
    fill(store)
    store.close()
    conn = sqlite3.connect(tmp_path / "log.db")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE events SET payload = 'x' WHERE seq = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM events WHERE seq = 2")
    conn.close()


def test_raw_edit_is_caught_at_that_row(tmp_path: Path) -> None:
    store = Store(tmp_path / "log.db")
    fill(store, 4)
    store.close()  # checkpoints the WAL into the main file
    copy = tmp_path / "copy.db"
    shutil.copy(tmp_path / "log.db", copy)
    raw = copy.read_bytes()
    assert raw.count(b"tamper-me-0002") == 1
    copy.write_bytes(raw.replace(b"tamper-me-0002", b"tamper-me-9999"))
    result = Store(copy).verify()
    assert not result.ok and result.first_bad_seq == 3
    assert result.reason == "hash does not match the row"


def test_rewritten_hash_is_caught_at_the_next_row(tmp_path: Path) -> None:
    store = Store(tmp_path / "log.db")
    events = fill(store, 3)
    store.close()
    conn = sqlite3.connect(tmp_path / "log.db")
    conn.execute("DROP TRIGGER events_no_update")
    edited = {**events[1].hashed_row(), "payload": {"n": 1, "note": "edited"}}
    conn.execute(
        "UPDATE events SET payload = ?, hash = ? WHERE seq = 2",
        (jcs(edited["payload"]).decode(), chain_hash(events[0].hash, edited)),
    )
    conn.commit()
    conn.close()
    result = Store(tmp_path / "log.db").verify()
    assert not result.ok and result.first_bad_seq == 3


def test_export_lines_are_canonical_rows(tmp_path: Path) -> None:
    store = Store(tmp_path / "log.db")
    events = fill(store, 2)
    lines = list(store.export_lines())
    assert [json.loads(line)["hash"] for line in lines] == [e.hash for e in events]
    assert lines[0].encode() == jcs(events[0].as_dict())


def test_listeners_see_each_append(tmp_path: Path) -> None:
    store = Store(tmp_path / "log.db")
    seen: list[int] = []
    store.subscribe(lambda e: seen.append(e.seq))
    fill(store, 2)
    assert seen == [1, 2]


def test_ask_counts_and_pins(tmp_path: Path) -> None:
    store = Store(tmp_path / "log.db")
    assert [store.bump_ask_count("2026-10-12", "work", "personal") for _ in range(3)] == [1, 2, 3]
    assert store.ask_counts("2026-10-12", "work") == {"personal": 3}
    assert store.ask_counts("2026-10-13", "work") == {}
    assert store.pin("x.y") is None
    store.set_pin("x.y", "a" * 64)
    store.set_pin("x.y", "b" * 64)
    assert store.pin("x.y") == "b" * 64


def test_spend_sums_decimal_strings(tmp_path: Path) -> None:
    store = Store(tmp_path / "log.db")
    for cost in ("0.000100", "0.000250", None):
        store.append(
            session_id="s",
            agent="work",
            door="model",
            kind="model.call",
            payload={"cost_usd": cost},
        )
    assert store.spend_since("work", "2000-01-01T00:00:00.000Z") == Decimal("0.000350")
    assert store.spend_since("personal", "2000-01-01T00:00:00.000Z") == 0


def test_fingerprint_changes_with_description() -> None:
    schema = {"type": "object", "properties": {}}
    assert tool_fingerprint("t", "a", schema) == tool_fingerprint("t", "a", dict(schema))
    assert tool_fingerprint("t", "a", schema) != tool_fingerprint("t", "b", schema)


def test_holds_move_once_and_intents_claim_a_key(tmp_path: Path) -> None:
    from steward_core.store import Hold

    store = Store(tmp_path / "s.db")
    hold = Hold(
        hold_id="h-1",
        session_id="s-1",
        agent="work",
        kind="tool",
        tool="travel_book_train",
        tool_id="travel.book_train",
        args={"option_id": "train-0930"},
        fingerprint="f" * 64,
        rule="trifecta",
        rules=["trifecta"],
        reason="held",
        details={},
        choices=[{"id": "approve", "label": "Allow once"}],
        state="pending",
        created="2026-10-11T18:05:00.000Z",
        expires="2026-10-12T06:05:00.000Z",
    )
    store.insert_hold(hold)
    assert store.move_hold("h-1", from_state="pending", to_state="approved", choice="approve")
    assert not store.move_hold("h-1", from_state="pending", to_state="denied")
    assert store.hold("h-1").state == "approved"  # type: ignore[union-attr]
    assert [h.hold_id for h in store.holds(state="approved")] == ["h-1"]
    assert store.start_intent("k", "h-1", "travel.book_train", "2026-10-11T18:06:00.000Z")
    assert not store.start_intent("k", "h-1", "travel.book_train", "2026-10-11T18:07:00.000Z")
    store.finish_intent("k", "done", {"booking": "bk-1"}, "2026-10-11T18:06:01.000Z")
    assert store.intent("k")["state"] == "done"  # type: ignore[index]
    store.save_stand_in("s-1", "[ADDR-1]", {"token": "[ADDR-1]", "covers": 1})
    assert store.stand_in("s-1", "[ADDR-1]") == {"token": "[ADDR-1]", "covers": 1}
    assert store.stand_in("s-2", "[ADDR-1]") is None


def test_ask_counts_are_scoped(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    store.bump_ask_count("2026-10-11", "work", "personal", "live")
    assert store.bump_ask_count("2026-10-11", "work", "personal", "s-run") == 1
    assert store.ask_counts("2026-10-11", "work", "live") == {"personal": 1}
