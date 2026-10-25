"""The policy file (config/steward.yaml): agents, their passes, models, tools and contacts.

The file is committed and holds no secrets. Each agent names the environment variable that
holds its pass; the pass itself lives only in .env.

Every upstream tool on a pass needs a class in the tools section (Phase 2). Classes come from
this file, not from MCP annotations, because the MCP specification says to treat annotations as
untrusted hints. A tool without a class fails the load, so nothing reaches an agent unclassed.
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
RESERVED_TOOL_NAMES = frozenset({"ask", "reply", "hold_status"})

ToolClass = Literal["read", "write", "spend", "send", "change_paid", "destructive"]
# Classes that always wait for the owner, with the rule each one fires.
HOLD_CLASSES: dict[str, str] = {
    "send": "send_needs_owner",
    "change_paid": "paid_change_needs_owner",
    "destructive": "destructive_needs_owner",
}
# Classes that reach a third party, so they can carry data out (the trifecta's third leg).
EGRESS_CLASSES = frozenset({"send", "spend", "change_paid"})


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Price(_Config):
    """USD per million tokens, as decimal strings in the file."""

    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)


class Delivery(_Config):
    """How Steward starts a turn of this agent: to answer a request (OpenClaw), or to pass on a
    change notice or a decided hold (Hermes)."""

    kind: Literal["openclaw-exec", "hermes-runs", "mock"]
    sandbox: str | None = None
    url: str | None = None
    # Environment variable holding the API key for hermes-runs (never the key itself).
    key_env: str | None = None
    timeout_s: int = Field(default=120, gt=0, le=1800)


class AgentPolicy(_Config):
    pass_env: str
    model: str
    # How the agent's MCP client prefixes Steward's tools (Hermes: mcp__steward__,
    # OpenClaw: steward__). Only used to name tools exactly in the messages Steward writes.
    tool_prefix: str = ""
    tools: tuple[str, ...] = ()
    budget_usd_per_day: Decimal = Field(ge=0)
    # spend tools are allowed while a trip's total stays within this (GBP); above it they hold.
    trip_limit_gbp: Decimal | None = Field(default=None, ge=0)
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


class Versioned(_Config):
    """A write that names the version it read (optimistic concurrency, like HTTP If-Match)."""

    id_arg: str
    version_arg: str
    # Tool Steward calls with {id_arg: ...} to read the current item and its version.
    read_tool: str


class ToolRule(_Config):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    tool_class: ToolClass = Field(alias="class")
    # Returns the owner's private data: a result sets the agent's private_read flag.
    private: bool = False
    # Returns content from outside: a result sets the agent's untrusted_seen flag.
    untrusted: bool = False
    # Result field holding a sender address. The result counts as untrusted unless the
    # sender's domain is in trusted_mail_domains.
    trust_field: str | None = None
    # Can send data out. Defaults to true for send, spend and change_paid.
    egress: bool | None = None
    # Arguments that must carry a stand-in such as [ADDR-1]; Steward swaps in the real value
    # only when it executes the call.
    stand_in_args: tuple[str, ...] = ()
    # spend: tool Steward calls with {"tool": <name>, "arguments": {...}} to price the call.
    quote_tool: str | None = None
    # spend: tool Steward calls with {} to list bookings already made (for the trip total).
    ledger_tool: str | None = None
    versioned: Versioned | None = None

    @property
    def is_egress(self) -> bool:
        return self.tool_class in EGRESS_CLASSES if self.egress is None else self.egress


class ContactRule(_Config):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    asker: str = Field(alias="from")
    contact: str = Field(alias="to")
    request_types: tuple[str, ...]
    # Busy blocks are widened outward to this grid before they cross.
    detail_minutes: int = Field(default=30, gt=0, le=240)
    daily_limit: int = Field(default=10, gt=0)
    # More than burst_max asks within burst_minutes are held (burst detection).
    burst_max: int | None = Field(default=5, gt=0)
    burst_minutes: int = Field(default=10, gt=0)

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


class HoldSettings(_Config):
    # A held call waits this long (story time) for the owner, then expires. The story needs a
    # paid change held at 23:30 to still be decidable at 07:35.
    expires_hours: float = Field(default=12, gt=0)
    # Signed approval links and Telegram buttons stop working after this (P2-D10).
    link_minutes: int = Field(default=30, gt=0)
    # Address stand-ins such as [ADDR-1] stop working after this.
    stand_in_hours: float = Field(default=24, gt=0)
    # When a hold of one of these classes is decided, Steward starts a run of the agent to tell
    # it (P2-D9 option B, used for the 07:35 approval).
    notify_classes: tuple[ToolClass, ...] = ("change_paid",)


class Policy(_Config):
    agents: dict[str, AgentPolicy]
    contacts: tuple[ContactRule, ...] = ()
    tools: dict[str, ToolRule] = Field(default_factory=dict)
    # Mail from these sender domains does not set untrusted_seen.
    trusted_mail_domains: tuple[str, ...] = ()
    never_share: tuple[str, ...] = ()
    # Exact strings the model door replaces with [MASKED-n] before a call leaves Steward.
    mask: tuple[str, ...] = ()
    prices: dict[str, Price] = Field(default_factory=dict)
    checker: Literal["deterministic", "jev", "nemotron-judge"] = "deterministic"
    checker_timeout_s: float = Field(default=10, gt=0)
    a2a: A2ASettings = A2ASettings()
    holds: HoldSettings = HoldSettings()

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
        unclassed = sorted({t for a in self.agents.values() for t in a.tools} - set(self.tools))
        if unclassed:
            raise ValueError(f"tools on a pass need a class in the tools section: {unclassed}")
        for tool_id, tool_rule in self.tools.items():
            if not TOOL_ID.match(tool_id):
                raise ValueError(f"tools keys must look like server.tool: {tool_id}")
            refs = [tool_rule.quote_tool, tool_rule.ledger_tool]
            refs += [tool_rule.versioned.read_tool] if tool_rule.versioned else []
            dangling = sorted(r for r in refs if r is not None and r not in self.tools)
            if dangling:
                raise ValueError(f"{tool_id} refers to tools without a class: {dangling}")
            if tool_rule.tool_class == "spend" and tool_rule.quote_tool is None:
                raise ValueError(f"spend tool {tool_id} needs a quote_tool")
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

    def tool_rule(self, tool_id: str) -> ToolRule:
        return self.tools[tool_id]


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
