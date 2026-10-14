# 0009. Agents and images

Status: Accepted (2026-10-14)

## Context

Steward should guard any agent, and the clearest proof is two agents from different makers behind one Steward. OpenShell requires images that run as a non-root user (ADR 0006). The official `nousresearch/hermes-agent` image starts s6-overlay as root and refuses to run its gateway as root, so it does not fit.

## Decision

The work agent is Hermes Agent (Nous Research, MIT licence). It is a native MCP client and has an API server with `/v1/runs`. The personal agent is OpenClaw.

Both run from custom images:

| Agent | Image | User | Main process | How Steward starts a run |
| --- | --- | --- | --- | --- |
| Hermes | `ubuntu:24.04`, Hermes installed with its installer (browser and computer-use tools skipped) | `ubuntu` | `hermes gateway run` | API server `/v1/runs`, reached through `openshell forward` on host loopback port 8642 |
| OpenClaw | `node:24-bookworm-slim`, installed with npm (needs Node 24.16 or later) | `node` | none (a login shell) | `openshell sandbox exec ... openclaw agent exec --json` |

Configs are baked into the images, because OpenShell cannot combine file uploads with a main command. Both configs point at `host.openshell.internal:8000` for the model and for the MCP tools, with only the pass placeholder as credential (ADR 0008).

OpenClaw's gateway mode (`openclaw gateway run`) is a Phase 1 option for driving it.

Fallback: if OpenClaw does not work within about four hours of the Phase 0 spike, the personal agent becomes a second Hermes sandbox from the same image, forwarded on port 8643.

## Consequences

- Two image builds to maintain.
- Both installs fetch code from the internet at build time (an installer script, npm). The versions are pinned once they work, for the same supply-chain reason as ADR 0003.
- A config change means an image rebuild.
- Hermes's built-in tools still exist inside the sandbox, but they can reach nothing except Steward. The Hermes API server exposes the whole toolset, including terminal commands, so port 8642 is never published.
- The "any agent" claim rests on two different makers. If the fallback is used, it rests on one maker with two sandboxes.

## Alternatives considered

Two Hermes agents from the start. Simpler, but a weaker "any agent" claim.

The official Hermes image running as root. OpenShell rejects it.
