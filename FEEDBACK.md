# Feedback log

A running log of feedback on Nebius Token Factory, Nebius AI Cloud, NVIDIA OpenShell, Nemotron models, Hermes and OpenClaw, kept from day one for the hackathon feedback section. Newest first.

Entry format: Date, Product, What happened, What I expected, Suggestion.

## Entries

- Date: 2026-10-21
- Product: Nebius Token Factory (Nemotron models)
- What happened: The Nemotron models return their reasoning text in different message fields. `nvidia/nemotron-3-super-120b-a12b` fills both `reasoning` and `reasoning_content` (same text); `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` fills only `reasoning` and leaves `reasoning_content` null. A client that reads `reasoning_content` gets Super's reasoning but silently loses Nano's.
- What I expected: (to fill in)
- Suggestion: (to fill in)

- Date: 2026-10-14
- Product: Nebius Token Factory
- What happened: Several serverless models, including `nvidia/Nemotron-3-Nano-Omni` and `nvidia/Llama-3_1-Nemotron-Ultra-253B-v1`, were removed on 31 Aug 2026. The docs recommend `nvidia/Nemotron-3_5-Lightning` as the replacement.
- What I expected: (to fill in)
- Suggestion: (to fill in)
