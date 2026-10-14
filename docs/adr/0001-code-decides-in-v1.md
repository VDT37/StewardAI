# 0001. Code decides in v1

Status: Accepted (2026-10-14)

## Context

Steward decides what one agent may tell another. The decider can be code or an AI model. Four facts argue against a model in v1.

- An AI judge can be talked round by new wording.
- LiteLLM's LLM-as-a-Judge guardrail lets a request through when the judge itself errors (it fails open).
- Each check costs one extra model call, so every message pays in money and delay.
- Models leak even when told not to. In the MAGPIE benchmark (Juneja et al., arXiv, October 2025), leading models leaked up to 35.1% (GPT-5) and 50.7% (Gemini 2.5 Pro) of sensitive information while working on shared tasks.

## Decision

Fixed rules in code, applied to typed facts (the validated fields of a request or reply), decide what passes. The verdict is allow, refuse or hold. A hold sends the user an approval card.

A helper model (Nemotron Nano) writes the morning brief and explains approval cards. It cannot approve anything.

If a check cannot run, nothing is shared or sent. Steward fails closed, and the brief tells the user.

## Consequences

- Decisions are deterministic and testable. The same input gives the same verdict, and each rule can have its own unit test.
- No model call per check, so no model cost or model delay on the decision path.
- The request types are narrow (see ADR 0002), so at first more requests reach the user as approval cards. Repeated approvals of the same kind can later suggest a new request type.
- The helper model sits outside the decision path. If its call fails, a template writes the brief (planned for Phase 3).
- Accepted ADRs are not edited. A changed decision gets a new ADR that supersedes the old one.

## Alternatives considered

None of these is adopted in v1. Each is measured against code in the Phase 4 ablations.

Nemotron judge. An LLM checks each request and reply. It can be talked round and costs a call per check. ADR 0005 adds a fail-closed wrapper, so a judge error becomes a hold.

Jev gatekeeper. TypeSafe AI's decision model scores each request and reply, and thresholds turn the score into allow, refuse or hold. It is closed and hosted only, so the text it checks goes to a third party.

Jev translator. Jev maps a free-text ask to one request type, and code still checks every field. It widens what agents can ask without letting free text cross. It is the strongest v2 candidate, worth adding if tests show the type list is too narrow.
