# 0004. Hash-chained flight log

Status: Accepted (2026-10-14). Updated 2026-10-24 when the log was built in Phase 1.

## Context

Steward has to show what each agent did and shared. That needs a record of every call, message, decision, cost and result, including each full agent-to-agent reply and what crossed.

Three needs follow. The record must show when it has been altered. A session must replay later without calling a model, because the demo video is recorded from a deterministic replay and every ablation arm replays the same recorded sessions. And the record holds sensitive content (the full replies before trimming), so it has to stay under the user's control.

Tarmac (a Qwen Cloud hackathon winner, Agent Society track) keeps a hash-chained event log that replays offline, byte for byte. Steward borrows the idea.

## Decision

One append-only table in PostgreSQL 16 records every call, message, decision, cost and result, including each full agent-to-agent reply and what crossed.

Each row stores:

```
hash = SHA-256(previous row's hash + the row's canonical JSON)
```

The canonical JSON follows RFC 8785 and leaves out the row's own hash. The first row chains from a fixed genesis value. How the previous hash is encoded when joined to the JSON (hex text or raw bytes) is fixed when this is built in Phase 1.

A verify endpoint recomputes the chain and compares it with the stored hashes. A JSONL export lets a session replay offline without any API keys.

Append-only is enforced in the database, either with grants (no UPDATE or DELETE for the app role) or with a trigger. The choice is made in Phase 1.

## Consequences

- Editing a row, or losing one, breaks the chain from that point, so verification shows it.
- The chain shows tampering. It does not prevent it. Someone with full write access to the table could rewrite a row and recompute every later hash. Grants or a trigger make that harder, and signed rows or an external log would close it. Neither is in v1.
- Replays are byte-for-byte repeatable, which the demo video and the ablations rely on.
- Replay needs no API keys, so anyone with the export can run it.
- The log holds sensitive content, so it stays on the user's own VM. Treat an export the same way.

## Alternatives considered

A plain table. The simplest option, with no tamper evidence.

Ed25519-signed rows. Stronger, because a rewrite would need the private key. It also means key storage and rotation to handle.

An external transparency log. Overkill for one person's agents.

## Update (2026-10-24): as built in Phase 1

The two points left open above are settled, and the store changed.

- Store: SQLite for v1, one file on the VM (`var/steward.db`, WAL mode), instead of PostgreSQL. Six days were left, the laptop cannot run Docker, and tests and CI then need no database service. The store sits behind one small class (`steward_core.store.Store`) and its SQL avoids SQLite-only types, so PostgreSQL can replace it later.
- Encoding: `hash = sha256(prev_hash_hex + "\n" + jcs(row))`, hex encoded. The previous hash joins as its 64 ASCII hex characters, then a newline, then the RFC 8785 canonical JSON of the row without its own hash: `{seq, ts, session_id, agent, door, kind, payload, prev_hash}`. The genesis value is 64 zeros. The payload column stores `jcs(payload)`, so parsing it back gives exactly the object that was hashed.
- Append-only: `BEFORE UPDATE` and `BEFORE DELETE` triggers abort with "flight log is append-only". Appends read the last hash and insert inside one `BEGIN IMMEDIATE` transaction behind one lock, so the chain cannot fork.
- Verify and export: `GET /admin/log/verify` recomputes the chain and names the first row that does not match; `GET /admin/log/export.jsonl` writes one canonical JSON row per line.

The triggers only guard SQL statements. A raw edit of the file gets past them, and verify then reports the edited row; the unit tests do exactly this.
