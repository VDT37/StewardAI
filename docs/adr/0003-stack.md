# 0003. Stack

Status: Accepted (2026-10-14)

## Context

Steward holds the keys its agents must not have, so it is a prime target. The LiteLLM releases 1.82.7 and 1.82.8 on PyPI (24 March 2026) were poisoned and stole credentials, including LLM API keys, from machines that installed them. Steward has the same exposure.

Two other constraints shape the stack. Features freeze on 25 October 2026, and the agents run in sandboxes that need a Docker host.

## Decision

| Layer | Choice |
| --- | --- |
| Language and packaging | Python 3.12, uv workspace (uv.lock committed) |
| Gateway and API | FastAPI, Pydantic v2 (strict models are the deterministic check) |
| Tool door | Official MCP Python SDK v2 (`from mcp.server import MCPServer`), Streamable HTTP |
| Model door | A thin httpx proxy, OpenAI-compatible, forwarding to Nemotron on Nebius Token Factory |
| Flight log | PostgreSQL 16 (see ADR 0004) |
| Background work | A worker process with APScheduler 3 (watcher, brief, red-team) |
| Console | React, TypeScript, Vite, Tailwind, shadcn/ui, TanStack Query, built with pnpm |
| Channels | Telegram Bot API for approval cards and the brief |
| Agent sandboxes | NVIDIA OpenShell for both agents (Hermes and OpenClaw), default-deny networking |
| Hosting | Docker Compose on one AWS EC2 t3.xlarge VM (4 vCPU, 16 GB RAM) |
| HTTPS | Caddy, when the public demo arrives in Phase 3 |

The agents run only inside sandboxes, never directly on the VM. Development is VM only for the agents and OpenShell. The laptop is for code, git, docs and tests.

The MCP SDK's app rejects any Host header outside an allowlist with HTTP 421. Sandboxes call Steward as `host.openshell.internal`, so the allowlist must include it, for example `transport_security=TransportSecuritySettings(allowed_hosts=["host.openshell.internal:*", "127.0.0.1:*", "localhost:*"])` from `mcp.server.transport_security`.

Dependencies are few and pinned: a lockfile (uv.lock), CI actions pinned by commit SHA, and no LiteLLM. Kubernetes, microservices and message queues are skipped.

## Consequences

- Steward writes its own model proxy. It must pass streaming tool-call chunks through. The plan lists this as a risk, with non-streaming mode for agents as the fallback.
- A wrong Host allowlist shows up as 421 errors from inside the sandboxes.
- Sandboxes reach Steward over plain HTTP on the same VM. HTTPS arrives with the public demo in Phase 3.
- OpenShell is pre-1.0 (release line 0.1.x), so sandbox setup sits on the critical path. The plan's fallback is plain containers.
- Every change goes from the laptop through git to the VM, because the agents and OpenShell run only there.
- Pinning lowers the risk of a poisoned release. It does not remove the exposure of holding every key.

## Alternatives considered

LiteLLM as the model door. It is a ready-made proxy, but it is a bigger dependency, and the March 2026 incident hit exactly this role. A thin httpx proxy has less code to trust.

NemoClaw instead of plain OpenShell. It duplicates Steward's model routing, tool gating and key custody. It forces HTTPS-only MCP (so Caddy and a hostname from the start) and pins an older OpenShell (0.0.116) while OpenShell is now 0.1.x.

Nebius Serverless Jobs for red-team and evals. Skipped for now. There are no AI Cloud credits (the hackathon FAQ says promo credits are for Token Factory only), so these runs stay on the VM.
