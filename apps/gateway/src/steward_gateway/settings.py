"""Settings from the environment, and the pass book that maps a bearer token to an agent."""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from steward_core.policy import Policy, load_policy

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1"


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
        return cls(
            nebius_api_key=values["NEBIUS_API_KEY"],
            owner_token=values["STEWARD_OWNER_TOKEN"],
            passes={name: values[agent.pass_env] for name, agent in policy.agents.items()},
            policy=policy,
            db_path=Path(source.get("STEWARD_DB_PATH", "var/steward.db")),
            console_dir=Path(source.get("STEWARD_CONSOLE_DIR", "apps/web/dist")),
            openshell_bin=source.get("STEWARD_OPENSHELL_BIN", "openshell"),
        )


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
