# 0008. Passes and placeholders

Status: Accepted (2026-10-14). Updated 2026-10-23 with what OpenShell can and cannot enforce.

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

## Update (2026-10-23): what OpenShell enforces and what only Steward can

The question was: why not let OpenShell manage the Nebius key on its own, injecting it into each sandbox the same way it injects the pass? This was checked against the OpenShell docs (v0.1.5 at the time).

OpenShell decides which program may reach which host, port, method and path, and with which credential. It does not read request bodies or responses: for `protocol: rest` it checks method, path and query parameters only.

| Rule | OpenShell | Steward |
| --- | --- | --- |
| The agent cannot read the real credential | Yes (placeholder swap) | Yes (the key never leaves Steward) |
| Only listed programs reach the endpoint | Yes (`binaries`) | No |
| Nothing but Steward is reachable | Yes (default-deny network) | No |
| Each agent may use only its own model | No. The model is a field in the JSON body, and "Provider attachment does not select or rewrite a model". The old managed inference route that set one model for every sandbox (`openshell inference set`) has been removed | Yes |
| A spend cap per agent | No. The docs mention no token counting, metering, budgets or quotas | Yes |
| A log of model, tokens and tool calls | No. Sandbox logs record allowed and denied connections | Yes |
| Which MCP tools an agent may call | Partly. `mcp` rules match the method and tool name, not the arguments | Yes |
| Trimming tool and agent-to-agent replies | No | Yes |

A hijacked agent is still an allowed program talking to an allowed endpoint, so OpenShell lets its requests through. It does not need to steal the key; it uses it. Rules about what a request asks for and what comes back need something that reads the message, which is Steward. In one line: OpenShell protects the credentials; Steward governs how they are used.

Where the key travels also differs. With OpenShell injecting the Nebius key, the agent's own request leaves the container carrying the real key, written in by the supervisor that runs beside the agent. With Steward, only the pass leaves the container; Steward makes a separate HTTPS request to Token Factory, so the key exists only on the host.

Planned hardening, not decided yet: in Phase 3, before the public demo, run Steward in its own OpenShell sandbox and keep the Nebius key in an OpenShell provider. OpenShell would then hold every real credential and Steward's own code would never see one. Open point: the agent sandboxes must reach Steward's sandbox through a port forward (`--forward 0.0.0.0:8000` or `--expose`), which is untested. This would get its own ADR.

Sources: [inference](https://docs.nvidia.com/openshell/latest/how-it-works/inference.md), [network rules](https://docs.nvidia.com/openshell/latest/how-it-works/policies/network-rules.md), [provider profiles](https://docs.nvidia.com/openshell/latest/how-it-works/providers/profiles.md).

## Alternatives considered

OpenShell alone injects the Nebius key into each sandbox, with no model door in Steward. It protects the key as well, and the agent-to-agent trimming would still work, because that never needs the key. It loses the per-agent model, the spend caps and the model-call log (see the update above).

A Nebius key per agent. There are no per-call rules, no masking and no central log, and it defeats the point of Steward holding the keys.

The pass as a plain environment variable. It works without OpenShell, but a prompt injection could make the agent print it.

LiteLLM-style virtual keys. The same idea, with a bigger dependency (ADR 0003).
