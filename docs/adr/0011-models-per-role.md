# 0011. Models per role

Status: Accepted (2026-10-14). Updated 2026-10-28 when two of the models were announced for removal.

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

## Update (2026-10-28): replacement models

Token Factory's notice says `nvidia/nemotron-3-super-120b-a12b` and `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` leave Serverless on 9 Nov 2026 (https://docs.tokenfactory.nebius.com/model-deprecations-november-2026). There is no automatic reroute. The deprecation point in the Consequences held: the switch was a change of IDs in the policy file (`config/steward.yaml`, where the IDs live as built, not in `.env`) plus the spike below.

| Role | Model ID |
| --- | --- |
| Work agent | `nvidia/Nemotron-3.5-Super-VL` |
| Personal agent | `nvidia/Nemotron-3_5-Lightning` |
| Morning brief (the helper) | `nvidia/Nemotron-3_5-Lightning`, with `reasoning_effort` set to `none` |

- Prices are unchanged by the switch, in USD per million input and output tokens: Super VL 0.30 and 0.90, Lightning 0.06 and 0.24. They match the models they replace.
- Spike: `spikes/results/phase3-models.txt`, run on the VM on 28 Oct with `spikes/token_factory_check.py`. Both models pass the plain call, streaming, tools, and streaming with tools. Lightning with `reasoning_effort` set to `none` returns no reasoning tokens in the plain call (7 completion tokens, 235 ms). At its default effort the same plain call used 322 reasoning tokens (1277 ms).
- Helper role: the morning brief is worded by Lightning with `reasoning_effort` `none`, at most 400 tokens, a 30 second timeout and a budget of 0.20 USD a day (the `helper` section of the policy). Code picks the brief's facts, checks its lines and writes its one question, so the helper cannot change a decision (ADR 0001). In the live evening bundled for the public demo the brief call took 765 ms and cost 0.000095 USD.
- Card explanations, listed above as a helper role, are not a model's job as built: approval cards are written by code, and the brief is the helper's only use.
- Old prices stay in the policy for the two removed IDs, so recordings made with them meter the same in replay.
- Open: the AI-check arm above names `nvidia/nemotron-3-super-120b-a12b`, which leaves on 9 Nov. Its model is not chosen until Phase 4 starts.
