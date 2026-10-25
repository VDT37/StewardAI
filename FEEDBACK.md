# Feedback log

A running log of feedback on Nebius Token Factory, Nebius AI Cloud, NVIDIA OpenShell, Nemotron models, Hermes and OpenClaw, kept from day one for the hackathon feedback section. Newest first.

Entry format: Date, Product, What happened, What I expected, Suggestion.

## Entries

- Date: 2026-10-25
- Product: Tavily Extract API
- What happened: Extract returns text that a visitor to the page cannot see. I put one marker string in each of eight hiding methods on a test page (https://vdt37.github.io/StewardAI/demo/hiding-test.html) and called extract four times: basic and advanced depth, each as markdown and as text. Text hidden with CSS (`display:none`, `visibility:hidden`, `font-size:0`, off-screen positioning, same colour as the background) and `aria-hidden` text came back in all four combinations. HTML comments and `<noscript>` content did not come back in any of them. `include_usage` reported 0, 0, 0 and 1 credits for the four calls. This matters for Steward because a page's hidden instructions reach an agent through extract, so a gateway cannot rely on the extractor to strip them. Steward treats every extract result as untrusted.
- What I expected: Text that is not visible on the page to be left out of the result, or marked as hidden. (draft, edit)
- Suggestion: State in the docs which hidden-text methods extract keeps, and offer an option to return visible text only. (draft, edit)

- Date: 2026-10-24
- Product: NVIDIA OpenShell 0.1.4 (CLI)
- What happened: Changing an imported provider profile took three tries. `openshell profile lint -f` on the edited file failed with "custom provider profile 'steward-work' already exists", so lint cannot check an edit to a profile that is already registered. `openshell profile update -f <file> steward-work` then failed with "custom provider profile update requires a non-zero resource_version; export the current profile before editing it". It worked after prepending `resource_version: <n>` from `openshell profile export steward-work` to a copy of the file. The profile in the repo stays without `resource_version`, because the number belongs to the server.
- What I expected: (to fill in)
- Suggestion: (to fill in)

- Date: 2026-10-24
- Product: OpenClaw 2026.10.5 and Hermes Agent 0.22.2
- What happened: Trimming each agent's built-in tools cut prompt tokens per model call by a large factor. OpenClaw with `tools.profile: "minimal"`, `alsoAllow: ["steward__*"]` and `deny: ["session_status", "gateway"]` went from about 31k to 3.1k to 4.5k prompt tokens per call (`openclaw agent exec` defaults to the `coding` profile). Hermes with `agent.disabled_toolsets` listing the 13 default toolsets went from about 13k to 3.3k to 5.1k. Both still called the Steward tools correctly.
- What I expected: (to fill in)
- Suggestion: (to fill in)

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
