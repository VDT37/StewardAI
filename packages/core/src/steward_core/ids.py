"""Deterministic ids for requests, holds, calls and notices.

An id is the prefix and the first 12 hex characters of sha256("<session>/<prefix>/<n>"), where n
counts that prefix within the session. Replay runs under the recorded session id and makes the
same calls in the same order, so it mints the same ids and its decision events can be compared
with the live run byte for byte. Ids are labels, not secrets: knowing one gives an agent nothing,
because every door checks who is calling.
"""

from __future__ import annotations

from collections import Counter

from steward_core.canonical import sha256_hex


class IdMint:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._counts: Counter[str] = Counter()

    def number(self, prefix: str) -> int:
        """The next number for prefix (1, 2, ...) in this session."""
        self._counts[prefix] += 1
        return self._counts[prefix]

    def next(self, prefix: str) -> str:
        n = self.number(prefix)
        digest = sha256_hex(f"{self.session_id}/{prefix}/{n}".encode())
        return f"{prefix}-{digest[:12]}"
