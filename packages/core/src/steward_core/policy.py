"""The policy file (config/steward.yaml): agents, their passes, models, tools and contacts.

The file is committed and holds no secrets. Each agent names the environment variable that
holds its pass; the pass itself lives only in .env.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TOOL_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
# Exposed tool names drop the side prefix of the source: work_calendar.list_events becomes
# calendar_list_events on whichever side's endpoint lists it.
SIDE_PREFIXES = ("work_", "personal_", "shared_")
# Names Steward itself serves on the tool door; no upstream tool may take them.
RESERVED_TOOL_NAMES = frozenset({"ask", "reply"})


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Price(_Config):
    """USD per million tokens, as decimal strings in the file."""

    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)


class Delivery(_Config):
    """How Steward hands an agent-to-agent request to this agent."""

    kind: Literal["openclaw-exec", "mock"]
    sandbox: str | None = None
    timeout_s: int = Field(default=120, gt=0, le=600)


class AgentPolicy(_Config):
    pass_env: str
    model: str
    # How the agent's MCP client prefixes Steward's tools (Hermes: mcp__steward__,
    # OpenClaw: steward__). Only used to name tools exactly in the messages Steward writes.
    tool_prefix: str = ""
    tools: tuple[str, ...] = ()
    budget_usd_per_day: Decimal = Field(ge=0)
    delivery: Delivery | None = None

    @field_validator("tools")
    @classmethod
    def _tool_ids(cls, tools: tuple[str, ...]) -> tuple[str, ...]:
        bad = [t for t in tools if not TOOL_ID.match(t)]
        if bad:
            raise ValueError(f"tools must look like server.tool: {bad}")
        exposed = [exposed_name(t) for t in tools]
        clashes = sorted({n for n in exposed if exposed.count(n) > 1})
        if clashes:
            raise ValueError(f"two tools would be exposed under the same name: {clashes}")
        reserved = sorted(set(exposed) & RESERVED_TOOL_NAMES)
        if reserved:
            raise ValueError(f"tool names reserved for Steward: {reserved}")
        return tools


class ContactRule(_Config):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    asker: str = Field(alias="from")
    contact: str = Field(alias="to")
    request_types: tuple[str, ...]
    # Busy blocks are widened outward to this grid before they cross.
    detail_minutes: int = Field(default=30, gt=0, le=240)
    daily_limit: int = Field(default=10, gt=0)

    @field_validator("detail_minutes")
    @classmethod
    def _divides_day(cls, minutes: int) -> int:
        if 1440 % minutes:
            raise ValueError("detail_minutes must divide a day (1440 minutes) evenly")
        return minutes


class A2ASettings(_Config):
    # How long an ask waits for the reply before it returns "no answer" (fail closed).
    reply_timeout_s: float = Field(default=150, gt=0)
    # After the delivery run ends without a reply, wait this long before giving up.
    reply_grace_s: float = Field(default=3, ge=0)


class Policy(_Config):
    agents: dict[str, AgentPolicy]
    contacts: tuple[ContactRule, ...] = ()
    never_share: tuple[str, ...] = ()
    # Exact strings the model door replaces with [MASKED-n] before a call leaves Steward.
    mask: tuple[str, ...] = ()
    prices: dict[str, Price] = Field(default_factory=dict)
    checker: Literal["deterministic", "jev", "nemotron-judge"] = "deterministic"
    checker_timeout_s: float = Field(default=10, gt=0)
    a2a: A2ASettings = A2ASettings()

    @model_validator(mode="after")
    def _consistent(self) -> Policy:
        names = set(self.agents)
        for rule in self.contacts:
            unknown = {rule.asker, rule.contact} - names
            if unknown:
                raise ValueError(f"contact rule names unknown agents: {sorted(unknown)}")
            if rule.asker == rule.contact:
                raise ValueError("an agent cannot be its own contact")
        envs = [a.pass_env for a in self.agents.values()]
        if len(set(envs)) != len(envs):
            raise ValueError("each agent needs its own pass_env")
        missing = sorted({a.model for a in self.agents.values()} - set(self.prices))
        if missing:
            raise ValueError(f"no price for models: {missing}")
        if any(not entry for entry in self.mask):
            raise ValueError("mask entries must not be empty")
        return self

    def route(self, asker: str, contact: str) -> ContactRule | None:
        for rule in self.contacts:
            if rule.asker == asker and rule.contact == contact:
                return rule
        return None

    def contacts_of(self, asker: str) -> list[ContactRule]:
        return [rule for rule in self.contacts if rule.asker == asker]

    def askers_of(self, contact: str) -> list[ContactRule]:
        return [rule for rule in self.contacts if rule.contact == contact]


def exposed_name(tool_id: str) -> str:
    """server.tool -> the name an agent sees: the server without its side prefix, _, the tool."""
    server, _, tool = tool_id.partition(".")
    for prefix in SIDE_PREFIXES:
        if server.startswith(prefix) and len(server) > len(prefix):
            server = server[len(prefix) :]
            break
    return f"{server}_{tool}"


def load_policy(path: Path | str) -> Policy:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Policy.model_validate(data)
