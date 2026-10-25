"""What a typed reply looks like after Steward's rules, shared by every request type."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class Trimmed:
    reply: BaseModel
    # Names of the rules that changed something, in the order they ran.
    rules_fired: list[str] = field(default_factory=list)
    # Sorted names of the fields that were present and did not cross.
    dropped_fields: list[str] = field(default_factory=list)
