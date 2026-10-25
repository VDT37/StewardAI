"""Settings from the environment, and the pass book that maps a bearer token to an agent."""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from steward_core.policy import Policy, load_policy

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1"
DEMO_PAGE_URL = "https://vdt37.github.io/StewardAI/demo/piccadilly-step-free.html"


@dataclass(frozen=True)
class Settings:
    nebius_api_key: str
    owner_token: str
    # Agent name -> its pass (a secret; never logged).
    passes: Mapping[str, str]
    policy: Policy
    db_path: Path = Path("var/steward.db")
    console_dir: Path = Path("apps/web/dist")
    upstream_url: str = TOKEN_FACTORY_URL
    openshell_bin: str = "openshell"
    # Optional (Phase 2). Without a Tavily key the web tools read the fixture pages.
    tavily_api_key: str | None = None
    web_backend: str | None = None
    # The public copy of the poisoned station guide (GitHub Pages, P2-D5 A).
    demo_page_url: str = DEMO_PAGE_URL
    # Signs approval links (P2-D10); links are off without it.
    approval_secret: str | None = None
    public_url: str = "http://localhost:8000"
    # Telegram approval cards; off unless both are set.
    telegram_token: str | None = None
    telegram_owner_id: int | None = None
    # Agent name -> API key for its delivery (hermes-runs), read from the env var it names.
    delivery_keys: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Read the policy file and the secrets it names. Fails on anything missing or reused."""
        source = os.environ if env is None else env
        policy = load_policy(source.get("STEWARD_POLICY", "config/steward.yaml"))
        names = ["NEBIUS_API_KEY", "STEWARD_OWNER_TOKEN"]
        names += [agent.pass_env for agent in policy.agents.values()]
        values = {name: source.get(name, "").strip() for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError("missing or empty settings: " + ", ".join(missing))
        tokens = [values[name] for name in names[1:]]
        if len(set(tokens)) != len(tokens):
            raise RuntimeError("the owner token and every pass must all differ")
        delivery_keys = {}
        for name, agent in policy.agents.items():
            env_name = agent.delivery.key_env if agent.delivery else None
            if env_name and source.get(env_name, "").strip():
                delivery_keys[name] = source[env_name].strip()
        owner_id = source.get("STEWARD_TELEGRAM_OWNER_ID", "").strip()
        return cls(
            nebius_api_key=values["NEBIUS_API_KEY"],
            owner_token=values["STEWARD_OWNER_TOKEN"],
            passes={name: values[agent.pass_env] for name, agent in policy.agents.items()},
            policy=policy,
            db_path=Path(source.get("STEWARD_DB_PATH", "var/steward.db")),
            console_dir=Path(source.get("STEWARD_CONSOLE_DIR", "apps/web/dist")),
            openshell_bin=source.get("STEWARD_OPENSHELL_BIN", "openshell"),
            tavily_api_key=_optional(source, "TAVILY_API_KEY"),
            web_backend=_optional(source, "STEWARD_WEB_BACKEND"),
            demo_page_url=_optional(source, "STEWARD_DEMO_PAGE_URL") or DEMO_PAGE_URL,
            approval_secret=_optional(source, "STEWARD_APPROVAL_SECRET"),
            public_url=_optional(source, "STEWARD_PUBLIC_URL") or "http://localhost:8000",
            telegram_token=_optional(source, "TELEGRAM_BOT_TOKEN"),
            telegram_owner_id=int(owner_id) if owner_id.lstrip("-").isdigit() else None,
            delivery_keys=delivery_keys,
        )


def _optional(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(name, "").strip()
    return value or None


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


@dataclass
class PassBook:
    """Which agent a pass belongs to, and whether that pass is suspended.

    Suspending a pass is the owner's kill switch for one agent (ADR 0008: revoking a pass is a
    Steward change, the Nebius key is not touched). It lasts until resumed or Steward restarts.
    """

    passes: Mapping[str, str]
    owner_token: str
    suspended: set[str] = field(default_factory=set)

    def identify(self, authorization: str | None) -> str | None:
        token = bearer_token(authorization)
        if token is None:
            return None
        found: str | None = None
        for agent, value in self.passes.items():
            if secrets.compare_digest(token.encode(), value.encode()):
                found = agent
        return found

    def is_owner(self, authorization: str | None) -> bool:
        token = bearer_token(authorization)
        return token is not None and secrets.compare_digest(
            token.encode(), self.owner_token.encode()
        )
