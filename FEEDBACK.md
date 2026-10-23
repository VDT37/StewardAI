# Feedback log

A running log of feedback on Nebius Token Factory, Nebius AI Cloud, NVIDIA OpenShell, Nemotron models, Hermes and OpenClaw, kept from day one for the hackathon feedback section. Newest first.

Entry format: Date, Product, What happened, What I expected, Suggestion.

## Entries

- Date: 2026-10-23
- Product: OpenClaw 2026.10.5
- What happened: With a custom OpenAI-compatible provider on a private address (`host.openshell.internal`), OpenClaw turned on Tool Search automatically and hid all tools behind `tool_search`, `tool_describe` and `tool_call`. Nemotron Nano often skipped the search and called the bare MCP tool name (`list_calendar_events`), which OpenClaw could not resolve, so the run ended with the model saying it had no calendar access (3 of 3 runs). `tools.toolSearch: false` fixed it (3 of 3 runs called `steward__list_calendar_events`), at the cost of about 31k prompt tokens per model call.
- What I expected: (to fill in)
- Suggestion: (to fill in)

- Date: 2026-10-23
- Product: NVIDIA OpenShell 0.1.4
- What happened: OpenClaw's SQLite agent database failed inside the sandbox with `unable to open database file`. SQLite writes temp files to `/var/tmp` before `/tmp`; the sandbox policy did not allow `/var/tmp`, and setting `TMPDIR=/tmp` and `SQLITE_TMPDIR=/tmp` in the image had no effect because neither variable reached the sandbox's processes (the image's `PATH` did). Adding `/var/tmp` to `read_write` fixed it.
- What I expected: (to fill in)
- Suggestion: (to fill in)

- Date: 2026-10-23
- Product: NVIDIA OpenShell 0.1.4 (CLI)
- What happened: Two scripting surprises. `openshell sandbox exec` reads standard input, so inside a heredoc script it swallows the following lines unless given `</dev/null`. `openshell forward start ... -d` keeps the caller's output open, so `openshell forward start 8642 work-agent -d | tail` never returns; redirecting its output to a file works.
- What I expected: (to fill in)
- Suggestion: (to fill in)

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
