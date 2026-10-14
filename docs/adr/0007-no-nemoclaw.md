# 0007. No NemoClaw

Status: Accepted (2026-10-14)

## Context

NemoClaw is NVIDIA's guided reference stack on top of OpenShell. It brings:

- an onboarding wizard
- managed inference and a model router
- managed MCP servers
- messaging channels
- a hardened image (build tools and netcat removed, a 512-process limit)
- state-preserving rebuilds
- blueprint versioning

The original plan ran OpenClaw through NemoClaw.

Steward already does model routing, the MCP gateway, key custody and channels, so most of that would duplicate it. Three more facts count against it:

- NemoClaw's managed MCP accepts only HTTPS URLs and rejects loopback and host aliases. Sandboxes reach Steward as `host.openshell.internal`, so this would force Caddy and a public hostname in Phase 0.
- NemoClaw pins OpenShell 0.0.116, while OpenShell is now 0.1.x.
- It is alpha and wants more RAM (it recommends 16 GB for itself).

## Decision

NemoClaw is not used. Steward uses OpenShell directly: the "OpenShell path" that NVIDIA's own docs describe, where you bring your own container, policy file and provider setup.

What we give up: the hardened image, automatic credential setup, state-preserving rebuilds and the "NVIDIA reference stack" label.

## Consequences

- We write our own policy, provider profiles and images (ADRs 0006, 0008 and 0009). The walls come from OpenShell's policy.
- A smaller VM is enough (ADR 0010).
- Fewer moving parts, and no HTTPS or hostname needed until the public demo in Phase 3.
- Steward stays on the current OpenShell release line instead of the older one NemoClaw pins.
- NemoClaw, or only its image hardening, can be added later as a comparison if time allows.

## Alternatives considered

NemoClaw for OpenClaw only (the original plan). It carries the same costs for one agent, and the other agent would still need our own policy and image.

NemoClaw for both agents (nemohermes). Every reason above applies twice.
