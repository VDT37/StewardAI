# 0008. Passes and placeholders

Status: Accepted (2026-10-14)

## Context

A compromised agent leaks whatever it holds. A prompt injection can make an agent reveal a key, and agents may log secrets by accident. If an agent holds a real provider key, it can also spend money with no per-call rules.

A pass is a Steward-issued token that only means "this request comes from agent X". Steward maps it to what X may do (in Phase 0 that is which model; later, tools, budget and hours). The pass itself is still a secret worth protecting, because anyone who holds it can act as that agent.

## Decision

Real provider keys (the Nebius Token Factory key, later Tavily) exist only in Steward on the host.

Each agent gets a pass: a random token with an `stw_` prefix. It only tells Steward which agent is calling, and it can be revoked without rotating the real key.

The pass never enters the sandbox:

- An OpenShell provider profile declares the pass as a bearer credential (ADR 0006).
- Inside the sandbox, the agent's environment variable (`STEWARD_PASS_WORK` or `STEWARD_PASS_PERSONAL`) holds an opaque placeholder. The agent's config refers to `${STEWARD_PASS_WORK}` or `${STEWARD_PASS_PERSONAL}`.
- OpenShell's proxy swaps in the real pass only on requests to the profile's endpoints: `host.openshell.internal:8000`, at the allowed paths.
- The provider is attached when the sandbox is created, so the agent's process starts with the placeholder.
- Providers are created from the host with only the one pass each needs, so no other secret enters the process.

## Consequences

- An agent that reads its own environment sees only a placeholder that works nowhere else. Phase 0 checks this: the value inside the sandbox does not start with `stw_`.
- Passes are separate per agent, so one leak does not expose the other.
- Revoking a pass is a Steward change. The Nebius key is not touched.
- One exception, accepted: the Hermes API server key is passed as a plain `--env` value, so Hermes itself can read it, and it is briefly visible in the host's process list. That is acceptable because the server is reachable only through the host-loopback forward (ADR 0009).
- The swap works only on traffic that goes through OpenShell's proxy. Without the sandbox (ADR 0006) the placeholder idea does not apply.

## Alternatives considered

A Nebius key per agent. There are no per-call rules, no masking and no central log, and it defeats the point of Steward holding the keys.

The pass as a plain environment variable. It works without OpenShell, but a prompt injection could make the agent print it.

LiteLLM-style virtual keys. The same idea, with a bigger dependency (ADR 0003).
