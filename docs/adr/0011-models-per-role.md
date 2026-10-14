# 0011. Models per role

Status: Accepted (2026-10-14)

## Context

The hackathon rules require a runtime call to Nebius Token Factory. The same Nemotron model from another provider does not count. Agents must not call models themselves, because a direct call bypasses Steward (ADR 0006).

Token Factory removed several serverless models on 31 August 2026, including `nvidia/Nemotron-3-Nano-Omni`, and recommends `nvidia/Nemotron-3_5-Lightning` as the replacement. Model IDs can change under us, and the starting balance is $30.

## Decision

Every model call goes through Steward's model door to Nebius Token Factory. The model door picks the pass's model whatever the agent asks for.

| Role | Model ID |
| --- | --- |
| Work agent | `nvidia/nemotron-3-super-120b-a12b` |
| Personal agent, morning brief and card explanations | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, or `nvidia/Nemotron-3_5-Lightning` if Nano is no longer listed |
| Rare heavy reasoning (skill drafting, diagnosis) | `nvidia/Nemotron-3-Ultra-550b-a55b`, if listed |
| Red-team rewording and the AI-check ablation arm | `nvidia/nemotron-3-super-120b-a12b` |

Phase 0 (Part F) confirms each ID against the live model list (`GET /v1/models?verbose=true`) before it is relied on.

## Consequences

- The cheaper model handles the frequent calls (personal agent and brief), which keeps the $30 balance going.
- Model IDs live in config (`.env`), not in code. A deprecation means a config change.
- The agents' own configs also name a model, but the model door overrides it, so a wrong ID in an agent config does not change what runs.
- The helper model only explains and writes the brief (ADR 0001), so a change to it cannot change a decision.
- Using Super for the AI-check arm keeps the comparison on NVIDIA models (ADR 0005).

## Alternatives considered

One model for everything. Simpler, but either costlier or weaker.

Agents calling models directly. It bypasses Steward, and with the sandbox in place the call would be denied anyway (ADR 0006).
