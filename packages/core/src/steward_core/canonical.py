"""Canonical JSON (RFC 8785) and the hashes built on it.

Two JSON texts can mean the same object and still differ byte for byte (key order, spacing,
number format). A hash needs exactly one byte sequence per object, so everything Steward hashes
goes through jcs() first. JCS writes numbers as IEEE 754 doubles, so integers must stay within
2^53 and money travels as decimal strings, never floats.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import rfc8785

# prev_hash of the first row in the flight log.
GENESIS = "0" * 64


def jcs(obj: Any) -> bytes:
    """RFC 8785 canonical JSON as UTF-8 bytes. Raises on NaN, infinities and huge integers."""
    return rfc8785.dumps(obj)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def chain_hash(prev_hash: str, row: Mapping[str, Any]) -> str:
    """hash = sha256(prev_hash_hex + "\\n" + jcs(row_without_hash)), hex encoded.

    The previous hash joins as its 64 ASCII hex characters, then a newline, then the row's
    canonical JSON (this settles the encoding point left open in ADR 0004).
    """
    return sha256_hex(prev_hash.encode("ascii") + b"\n" + jcs(dict(row)))


def tool_fingerprint(name: str, description: str | None, input_schema: Mapping[str, Any]) -> str:
    """The pin for one MCP tool: sha256 of the canonical JSON of what the model gets to read."""
    return sha256_hex(
        jcs({"name": name, "description": description, "inputSchema": dict(input_schema)})
    )
