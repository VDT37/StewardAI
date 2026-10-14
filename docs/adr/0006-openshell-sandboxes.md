# 0006. OpenShell sandboxes

Status: Accepted (2026-10-14)

## Context

Steward is the guard at the door. It reads content and decides, but it only sees traffic sent to it. An agent with its own web or shell tools can post to the internet, call a model provider directly or read a key file, and Steward never sees any of it. The guard needs walls that make it the only route.

OpenShell is NVIDIA's open-source runtime for unpredictable agents, and the Personal AI track names it. A sandbox cannot read meaning, so it does not replace Steward. Each covers what the other cannot.

## Decision

Both agents run inside OpenShell sandboxes from Phase 0. OpenShell is used standalone (release line 0.1.x), installed with its own script. Its gateway runs as a systemd user service on host loopback (127.0.0.1:17670).

Each agent gets its own sandbox, a container on the Docker driver, with one base policy:

- No network rules, so all outbound traffic is denied.
- The working directory is read-write and the system paths are read-only.
- Landlock compatibility is `hard_requirement`. If the filesystem rules cannot be enforced, the sandbox refuses to start. It fails closed.

The only allowed destination is Steward at `host.openshell.internal:8000`. A per-agent provider profile adds it (ADR 0008). The rule covers `/v1/**` and the agent's own `/tools/<side>/**` path, and only for the agent's own executables. OpenShell ties network rules to real binary paths and hashes each executable, so a curl or a shell inside the sandbox is denied even towards Steward.

OpenShell never allows a sandbox to reach the host's loopback (nor link-local or unspecified addresses). That is why Steward listens on 0.0.0.0 (ADR 0010). Every denied connection is logged with the binary path and the reason.

OpenShell rejects images that run as root, so both agents use custom images (ADR 0009).

## Consequences

- Steward's claims become enforceable. An agent that ignores Steward has nowhere else to go.
- The guard and the walls are separate layers, and each covers what the other cannot.
- The binary globs in the profiles are unconfirmed. The first denial in the sandbox log shows the real executable path, and the profile is fixed from it.
- Each agent's own install lives in its writable home in Phase 0, so an agent could modify its own install inside its sandbox. Accepted for now.
- Sandboxes reach Steward over plain HTTP on the VM.
- The VM kernel must have Landlock enabled, or the sandboxes do not start. Phase 0 checks this on the VM.
- OpenShell is pre-1.0, so the release is pinned and the CLI may change.
- The walls are not a separate ablation. Five bypass traps join the Phase 2 trap suite as pass/fail checks: read Steward's `.env`, read the other agent's pass, send data to an outside site, call Token Factory directly, and call the other agent's API. A small "walls" table may still go in the demo (open question).

## Alternatives considered

No sandbox. Plain processes are faster to start, but an agent can bypass Steward and read the key file. Rejected as unsafe once the traps run.

Docker containers on an internal network. This blocks egress at the network level, but there are no per-binary rules, no credential placeholders and no Landlock.

NemoClaw on top of OpenShell. See ADR 0007.
