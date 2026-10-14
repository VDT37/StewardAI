# 0005. Pluggable checker

Status: Accepted (2026-10-14)

## Context

Phase 4 compares code against two AI deciders: Jev (TypeSafe AI's hosted decision model) and a Nemotron judge. The comparison is only fair if the decider is the one thing that changes. Arize's comparison of Jev lacked this control: its agent's replies varied between runs, so differences in outcome could not be pinned on the checker.

The product path must stay deterministic and fail closed (ADR 0001). The comparison must not touch it.

## Decision

One checker interface. It checks a request, checks a reply, and returns a verdict (allow, refuse or hold) plus the rule that fired.

Four implementations, selected by config:

- Deterministic: the default, and the only one used in the product.
- Jev gatekeeper: Jev scores each request and reply. Allow at 0.9 or above, refuse at 0.1 or below, otherwise hold.
- Jev translator: Jev maps a free-text ask to one request type, and code still checks every field.
- Nemotron judge (AI check): Nemotron 3 Super judges each request and reply and returns a verdict and a trimmed reply as JSON.

Every ablation arm replays the same recorded sessions, so only the decider changes.

A fail-closed wrapper with a timeout sits around the checker. Any checker error or timeout becomes a hold, never an allow.

Build order:

- Phase 1: the interface and the deterministic checker, with Jev and AI-check stubs behind config.
- Phase 2: recorded sessions and the trap suite.
- Phase 4: the comparison.

## Consequences

- The ablations need wiring, not new building. Phase 4 adds the Jev and Nemotron implementations to an interface that already exists.
- Differences between arms come from the decider alone, because the recorded sessions are identical.
- A non-deterministic checker can be measured (verdicts that change across five identical runs) without touching the product path.
- Injected judge timeouts and errors test the wrapper. Each one must end in a hold.
- The Jev arm sends text to a third party, which breaks Steward's own privacy rule. It runs on synthetic data only, and that is reported as a cost.
- Until Phase 4 the Jev and AI-check entries are stubs. If time runs out, an arm can be cut and the interface stays.

## Alternatives considered

Hard-coding the deterministic rules. There would be no fair comparison later, and adding an arm would mean changing every call site.

Separate code paths per arm. The paths drift, and an arm can end up differing in more than the decider.
