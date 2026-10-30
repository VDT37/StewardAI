# Steward

Steward is a self-hosted gatekeeper between the AI agents you run and everything they can reach: models, tools and each other. It is built for the Nebius x NVIDIA Global AI Hackathon, Personal AI track.

Status: Phase 3 (overnight watcher, morning brief, contain controls and the public demo) and the lean Phase 4 (a comparison of Steward's code checks with a Nemotron judge) are built and released as v1.0.1, the hackathon submission (30 Oct 2026). Steward holds risky calls for your approval, applies the rules below in code, plays the Manchester evening live against the real agents and replays it from the flight log. Public demo: https://13-49-146-96.sslip.io (a recorded evening, nothing there calls a model). The Results section has the trap suite, the live evenings and the comparison. The red-team runs were cut for time.

## The problem

People now run more than one agent. A work agent has the work email and calendar. A personal agent has the personal Gmail and calendar. They are more useful when they coordinate, but today you connect them fully or not at all. Steward lets them share the constraint and keeps the reason private.

Example: the work agent plans a client trip to Manchester and asks the personal agent about 12 to 15 October.

| Full answer                                           | What Steward lets through                |
| ----------------------------------------------------- | ---------------------------------------- |
| Wed 14 Oct, 2:15pm, cardiology follow-up with Dr Shah | Wed 14 Oct, 2pm to 4pm, busy, can't move |

Steward guards against four things:

- Oversharing, as in the example above.
- Prompt injection that makes one agent ask the other for private data.
- Small answers pieced together into something private.
- Two agents overwriting the same calendar or list.

## How it works

Every agent reaches the outside world through one of three doors.

| Door           | What it is              | What it does                                                                                                                                                                                   |
| -------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Model          | OpenAI-compatible proxy | Sends every model call to Nemotron on Nebius Token Factory. Holds the real key, picks the model for each agent and meters spend.                                                               |
| Tool           | MCP gateway             | Each agent sees only the tools on its pass.                                                                                                                                                    |
| Agent-to-agent | Fixed request types     | Agents ask each other only for availability, travel limits, dietary and access needs, or an address stand-in for one booking. Code checks every request and trims every reply. Steward itself sends change notices. |

Plain code decides everything in version 1. The agents run on Nemotron, and Nemotron 3.5 Lightning words the morning brief from facts that code picks, then code checks the wording. Agents hold a pass and never a provider key. Anything risky is held for you as an approval card. Every call is recorded in a hash-chained log that can be replayed.

## Holds and approvals

A call that needs you is held, not run. Steward saves the exact call with a fingerprint (a sha256 over the agent, tool, arguments and hold id as canonical JSON) and answers the agent at once with the hold id. The agent can check on it with the `hold_status` tool. When you decide a paid change, Steward also starts a new run of the work agent with the result.

- Console queue: the Waiting for you page lists each hold as a card with the reason and the fingerprint. An approval names the hold and its fingerprint, and any other fingerprint voids it, so what you saw is what runs.
- Execute once: before an approved call goes out, Steward claims a unique key for it, so a second approval cannot run it again. A timeout or error leaves the call marked unknown for you to look at, and it is never retried.
- Expiry: a hold expires after 12 hours (`holds.expires_hours` in the policy).
- Signed links: `/a/{token}` opens a confirm page, and only the button on that page decides, because link previews open links on their own. Links last 30 minutes. They are off until `STEWARD_APPROVAL_SECRET` is set, and `STEWARD_PUBLIC_URL` sets the address a link points to.
- Telegram cards: each new hold is sent to your chat as a card of short blocks: Held for you (what is held), Why (the rule), Prompted by (what started the agent's run, from Steward's own records), Call (the tool, then one line per argument) and Locked to (the fingerprint). The choices are the buttons. The console card shows the same Prompted by line. Cards are off by default: set `TELEGRAM_BOT_TOKEN` and `STEWARD_TELEGRAM_OWNER_ID` in `.env` to turn them on. A button press counts only from your Telegram user id. A decided card is edited to end with a plain outcome line, for example Approved, and it ran once; Denied; or Expired unanswered. The morning brief is laid out the same way, with one bold topic per block and the question in its own block. Button tokens live in memory, so after a Steward restart older buttons say they have expired and you use the console. This has been tested with a real bot.
- Quiet hours: from 22:00 (`brief.quiet_from` in the policy) until the morning brief, a held call sends no card, and the brief asks you instead, with Approve and Deny buttons. If a call in the brief's question already has an open card, the brief says where to answer it and has no buttons. Once every call in a brief's question is decided anywhere, the brief is edited to end with the outcome (for example Denied) and loses its buttons. So you are asked about a held call once. Right after the brief, the other calls held overnight arrive as cards, so everything held overnight can be decided from the phone. Calls of one agent held by the same rule share one card whose buttons decide all of them (for example Deny (all 6)), and each call is still checked against its own fingerprint.

## Rules

The rules are plain code. A decision names every rule that applied, and the same facts always give the same answer, which is what lets replay check them.

| Rule | What it does |
| --- | --- |
| Tool classes | Every tool on a pass has a class in `config/steward.yaml`: `read`, `write`, `spend`, `send`, `change_paid` or `destructive`. `read` and `write` run. `spend` runs while the trip stays within its limit. `send`, `change_paid` and `destructive` are always held. Classes live in the policy because MCP annotations are untrusted hints. |
| Trifecta | For each agent, Steward tracks whether it has read private data and whether it has seen untrusted content (web results, or mail from a sender domain that is not trusted). With both set, every call that can send data out is held. The flags are set when a result returns, so the call that brings in the untrusted content still goes through. They clear at 04:00 London time or when you clear them. |
| Address stand-in | A request for your home address is always held. Approving it issues a token such as `[ADDR-1]`, valid for 24 hours and for one trip. Steward swaps in the real address when the booking goes out, and replaces the address with the token in every result the work agent receives. The "Taxi booking only" approval covers exactly one taxi booking for that trip. |
| Trip limit | A booking needs a quote, and Steward adds up the trip's bookings so far. A trip is one travel date. A booking that would take it past `trip_limit_gbp` (£300 for the work agent) is held. |
| Stale writes | A write to a versioned item, such as the shared to-do list, is checked against the item's current version. If the item changed since the agent read it, the write is held and the card shows both versions. |
| Budgets | Each agent has a daily budget in USD (`budget_usd_per_day`). At or over it, the model door answers HTTP 429 (`budget_exceeded`) and logs `model.budget_refused`. The call that crosses the budget still completes, because its cost is known only afterwards. |
| Daily limit and burst | An asker may send 10 asks a day to a contact, and no more than 5 within 10 minutes. Anything beyond either is held for you, not refused, and approving it runs it. |

## Request types and change notices

Agents ask each other through four request types. Each has a strict schema, a fixed message to the answering agent built from the typed fields only (nothing the asking agent wrote reaches it), and a trim step that decides what crosses.

| Request type | What crosses |
| --- | --- |
| `availability` | Busy blocks for a date range, widened to a 30-minute grid, with whether each can move. No titles, places or notes. |
| `travel_limits` | Limits such as "no departures before 9am on weekdays". The reason is dropped. |
| `needs` | Dietary and access needs as codes from a fixed list, such as vegetarian and step-free access. The reason is dropped. |
| `address_for_booking` | Nothing from the personal side. The ask is always held, and you answer it with a stand-in. Fields: `booking_tool`, `trip_date`, `pickup_time` and `booking_summary` (200 characters at most). |

A change notice is not a request type. When Steward answers an availability request, it keeps a watch on the window it answered. When the personal calendar changes, Steward re-reads it for each watch, trims it with the same code and sends the asker only the slots that changed, for example "Thu 15 Oct, 9am to 10:30am, now busy, can't move". The asker learns nothing about what changed or where. The notice is shown as from the personal side, and the personal agent does not send it. If several answered windows cover the same change, the asker gets one notice, delivered as a new run of the work agent.

## Web search with Tavily

The work agent has two web tools, `web_search` and `web_extract`. Steward defines them itself on top of Tavily's REST API (`tavily-python`, basic depth), so tool names and schemas stay fixed for pinning and replay, and the key stays in Steward. Set `TAVILY_API_KEY` in `.env` to use Tavily. Without a key the tools read the synthetic pages in the fixtures, which is what the tests and the trap suite use. `STEWARD_WEB_BACKEND` can force `tavily` or `fixture`. Replay never calls Tavily and returns the result recorded in the flight log.

Every result is wrapped as untrusted evidence and sets the agent's untrusted flag. That matters because extract returns text a visitor cannot see: text hidden with CSS (`display:none` and four other methods) and `aria-hidden` text came back in all four depth and format combinations tested, while HTML comments and `<noscript>` did not (details in [FEEDBACK.md](FEEDBACK.md)). The poisoned page hides its instruction with `display:none`. Live runs read a public copy on GitHub Pages, and replay and the trap suite keep the `.example` URL.

## Morning brief, watcher and contain

A scheduler runs inside Steward on Steward's clock, at London times. In a story run the brief comes at 07:30 story time, and in a live night at 07:30 London time. A job runs once per due time, and a live job that came due while Steward was down runs once after the restart. A nightly copy of the database (03:00, last 7 kept, in `var/backups`) runs in live sessions only.

The brief:

- Code picks the facts: per agent, the model and tool calls, asks, answers, refusals, holds and spend against budget; bookings; refused calls; asks held by the daily limit or burst check; change notices; stale writes; suspected phishing; watcher pauses; and whether the log chain is intact. They are typed values. Strings an attacker or an agent wrote (an email's subject and sender domain, a to-do item's title, the request type a refused ask named) are left out of what the model sees.
- Nemotron 3.5 Lightning words the facts as a few short lines. It runs with `reasoning_effort` set to `none`, at most 400 tokens, and a budget of 0.20 USD a day (the `helper` section of the policy).
- Code checks the lines. Every time, number and weekday must appear in the facts. No planted detail or address may appear unless the facts themselves carry it. No link, no question, no copied field name, no am or pm, and at most 900 characters.
- If a check fails, the model cannot be reached or its daily budget is used, a template written by code gives the lines instead.
- The one question is written by code, so the model cannot misstate what you approve. It is the trip's paid changes first (one question, so the train and the taxi go together), then a stale write, then the oldest other call that waits. Approve or Deny decides each call in it with that call's own fingerprint. A hold expires after 12 hours, so the brief asks only about calls still open.
- Quiet hours (see Holds and approvals) mean a call held from 22:00 sends no card while you sleep. It reaches you in the brief's question, or as a card right after the brief.

The watcher reads every new flight-log event and pauses an agent that crosses a hard limit, before that agent's next call. The limits are in the `watcher` section of the policy:

| Hard limit | Default |
| --- | --- |
| The same tool with the same arguments | 10 times in 5 minutes |
| Refused or denied calls | 8 in 10 minutes |
| Model calls | more than 60 in a minute |

A pause suspends the agent's pass at every door and puts a pause card in the console until you resume it. A resume starts that agent's windows again, so one legitimate retry after it does not pause the agent at once. A replay pauses at the same events, so pauses are checked like any other decision. Baselines only report: tonight's counts are compared with the agent's own earlier evenings (a z-score built from the median and the median absolute deviation, which ignore a few odd evenings, and which needs at least 3 earlier evenings), recorded with each brief and returned by `/admin/watcher`. They never pause anyone.

Contain controls, from the Agents page or the admin routes:

- Pause an agent, and resume it. Every call from a paused agent is turned away at the model, tool and agent-to-agent doors.
- Revoke an agent's pass. Only a new pass lifts it: put one in `.env` and in the agent's OpenShell provider, then restart Steward.
- Turn one tool off or on for one agent. A tool that is off is refused and hidden from the agent's tool list.

Every control is saved in the database, so a pause, a revoke or a tool that is off survives a Steward restart.

## Phishing screen

Each message a mail tool hands an agent is scored in code (`screen: mail` on both `read_message` tools in the policy), against signs a person is taught to look for. The verdict never comes from a model, so the same message always gets the same verdict.

| Sign | Weight |
| --- | --- |
| The sender's domain is not one you trust | 1 |
| The sender's domain is one or two edits from a trusted one | 3 |
| The sender's name poses as an administrator or IT team | 2 |
| It sets a deadline to act | 1 |
| It threatens to stop or close your account | 2 |
| It asks you to verify, confirm or sign in | 2 |
| A link's text hides where the link really goes | 2 |
| A link goes to a site unrelated to the sender and your domains | 1 |
| It contains text styled to be invisible | 2 |

A total of 4 or more is suspected phishing. For a suspected message:

- Every link is replaced by a numbered placeholder (`[LINK-1 withheld by Steward]`), so the agent cannot open what it never sees.
- Hidden text is dropped before the agent reads it, so it cannot follow an instruction a person would never see.
- A note from Steward tells the agent not to open links, reply or forward, and to report the message. The agent can do that with the `mail_report_message` tool (class `write`, on the work pass).
- The Today page lists the message with the signs that fired. You give a verdict: "It is phishing" keeps the links withheld, and "Not phishing, release its links" lets them through on the next read. The original stays in the flight log.

In the story, a "New Email Notification" from `Admin <contact@techsupport-corp.example>` arrives at 21:15, and the 22:40 lookalike email from `brightwater-logistlcs.example` is screened the same way.

## Sandboxes

Each agent runs inside its own NVIDIA OpenShell sandbox with a default-deny network. The only allowed destination is Steward, reached as `host.openshell.internal:8000`. The agent sees a placeholder for its pass, and OpenShell swaps in the real pass on the way out.

Steward is the guard at the door. OpenShell makes sure the door is the only way out. NemoClaw is not used because Steward already does model routing, tool gating and key custody.

## Architecture

```mermaid
flowchart LR
  subgraph SBW["OpenShell sandbox"]
    W["Work agent (Hermes)"]
  end
  subgraph SBP["OpenShell sandbox"]
    P["Personal agent (OpenClaw)"]
  end
  subgraph ST["Steward"]
    MD["Model door"]
    TD["Tool door"]
    AD["Agent-to-agent door"]
    RA["Rules and approvals"]
    FR["Flight recorder"]
  end
  NEB["Nebius Token Factory (Nemotron)"]
  TOOLS["Tools (MCP): mail, calendar, to-do, Tavily"]
  YOU["You: web console and Telegram (brief, approval cards)"]
  W --> ST
  P --> ST
  MD --> NEB
  TD --> TOOLS
  ST --> YOU
```

## Models

All models are served through Token Factory.

| Role                                        | Model                                | USD per million tokens (input, output) |
| ------------------------------------------- | ------------------------------------ | -------------------------------------- |
| Work agent (Hermes)                         | `nvidia/Nemotron-3.5-Super-VL`       | 0.30, 0.90                             |
| Personal agent (OpenClaw) and morning brief | `nvidia/Nemotron-3_5-Lightning`      | 0.06, 0.24                             |

Why these two: Token Factory removes `nvidia/nemotron-3-super-120b-a12b` and `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` from Serverless on 9 Nov 2026, with no automatic reroute. The switch was made on 28 Oct 2026, after a spike on the VM that ran each new model plain, streaming, with tools and streaming with tools. Both passed all four (`spikes/results/phase3-models.txt`). The prices are the same as the two models they replace, and the old prices stay in the policy so that recordings made with the old models meter the same in replay. The brief runs on Lightning with reasoning off. See [ADR 0011](docs/adr/0011-models-per-role.md).

The Phase 4 AI check (the Nemotron judge in Results) runs on these same two models, one arm each. The product never uses it. Token Factory also retired several other models on 31 Aug 2026.

## Other NVIDIA and Nebius tools

| Tool                              | Use                                          |
| --------------------------------- | -------------------------------------------- |
| NVIDIA OpenShell                  | Sandbox for each agent                       |
| Hermes Agent (Nous Research, MIT) | Work agent                                   |
| OpenClaw                          | Personal agent                               |
| Tavily                            | Search and page reading for the work agent   |

## Results

### Traps and tasks

All 49 traps and 13 normal tasks pass. The 46 traps that need no sandbox and the 13 tasks are played through a real Steward in process (`uv run pytest evals`). The 3 bypass traps need the real sandboxes and run on the VM (`bash evals/walls.sh`): 7 of 7 wall checks pass. The full test suite is 235 tests, all passing on 29 Oct.

### Live evenings

Nine evenings played with the real agents to the end, each replayed from its flight log. A replay passes when every decision of the evening comes out the same.

| Run | Models | Decisions matched in replay | Result |
| --- | --- | --- | --- |
| 1 | Nemotron 3 | 107 | PASS |
| 2 | Nemotron 3 | 140 | PASS |
| 3 | Nemotron 3 | 131 | PASS |
| 4 | Nemotron 3 | 125 | PASS |
| 5 | Nemotron 3 | 117 | PASS |
| 6 | Nemotron 3.5 | 152 | PASS |
| 7 | Nemotron 3.5 | 158 | PASS |
| 8 | Nemotron 3.5 | 156 | PASS |
| 10 | Nemotron 3.5 | 156 | PASS |

Runs 1 to 5 used the old Nemotron 3 models and cost about 0.10 USD an evening. Runs 6 to 10 used Nemotron 3.5 Super VL for the work agent and Nemotron 3.5 Lightning for the personal agent. Run 8 cost 0.19 USD and run 10 cost 0.15 USD. Run 10 is the bundled live evening of the public demo.

Real-time night (run 9): the whole evening at the clock's own pace, with the owner deciding in Telegram, started at 08:10 UK on 29 Oct. It showed the first live Approve in Telegram (the 19:06 train and address), the 21:15 phishing report, the 23:30 change notice with both paid changes held and the 01:30 stale write held. Steward was restarted on purpose at 19:07 UK to deploy the readable Telegram cards, which stopped the night before the morning brief, so it has no replay.

Demo night (run 10): the same story with each beat paced to a fixed London wall time (the 07:30 brief at 03:30 UK), started at 19:30 UK on 29 Oct with the owner deciding everything in Telegram. The train ran once and the taxi was booked with the stand-in [ADDR-1] at story 19:06. Result: the night's beats ran on time while the owner slept (no cards), the brief was written at 03:30 UK by Nemotron 3.5 Lightning and passed the check, and the calls held overnight arrived as cards right after it. The owner made every decision in Telegram: 4 approvals (the train and the address at 19:40, both paid changes from the brief at 03:45) and 7 denials (the 5 asks over the daily limit on one shared card, the burst ask and the stale write). Hermes reported both attack emails, the watcher paused nobody, the models cost 0.15 USD, and the replay matched 156 of 156 decisions. Run 10 is the bundled live evening of the public demo.

### Code checks against an AI check

Steward decides in code. To measure what that choice buys, the same 29 asks and 21 replies were decided twice: by Steward's code, and by a Nemotron judge. The cases come from the trap suite and the two bundled evenings. The judge gets the same policy in its prompt (the request types on the route with their schemas, the date window, the daily limit and the burst, that an address always needs the owner, and which fields may cross in a reply), and what it returns is what would cross, with no code trimming after it. Two judges were used, Nemotron 3.5 Super VL and Nemotron 3.5 Lightning, 3 runs each at temperature 0. Every arm is wrapped in fail-closed, so a judge error is a hold. The judge is never used by the product.

The comparison needs `NEBIUS_API_KEY` in `.env`, so it runs on the VM:

```bash
uv run --env-file .env python evals/ablation.py
```

It writes `evals/results/ablation.json` (every case and run) and `evals/results/ablation.md` (the table below). Each count covers the cases times 3 runs. The 29 asks are 8 attacks (24 checks), 11 that must wait for the owner (33 checks: an address request, the daily limit or the burst) and 10 normal asks (30 checks). The 21 replies give 63 checks.

| Measure | Code | Super VL judge | Lightning judge |
| --- | --- | --- | --- |
| Attack asks stopped | 24/24 | 23/24 | 21/24 |
| Owner holds kept | 33/33 | 27/33 | 27/33 |
| Normal asks allowed | 30/30 | 30/30 | 30/30 |
| Replies leaking a private detail | 0/63 | 0/63 | 0/63 |
| Private details leaked | 0 | 0 | 0 |
| Replies sent that fit the type | 57/57 | 57/57 | 53/55 |
| Availability replies with every busy day | 51/51 | 51/51 | 46/51 |
| Cases that change between runs | 0/50 | 3/50 | 10/50 |
| Judge errors (held) | 0 | 0 | 5 |
| Median ms per check | 0.068 | 4660.606 | 17132.33 |
| 95th percentile ms | 0.251 | 10528.908 | 47368.863 |
| USD per 1,000 checks | 0 | 1.4820 | 0.8173 |

When a timeout and an error were injected into the judge, each ended in a hold (`checker_error`).

1. Neither judge leaked a planted private detail in these 21 replies, given the policy in its prompt.
2. Both judges allowed asks the policy says to hold or refuse. Address requests must always wait for the owner, and each judge kept 27 of 33 owner holds (code kept 33 of 33). A 28-day calendar sweep got through although the prompt states the 14-day limit (Super VL in 1 of 3 runs, Lightning in all 3).
3. At temperature 0 the judges changed their decision or reply between identical runs on 3 (Super VL) and 10 (Lightning) of 50 cases. Code changed on 0.
4. A check took a median 4.7 s (Super VL) and 17 s (Lightning), against 0.07 ms for code, and cost 1.48 and 0.82 USD per 1,000 checks. Lightning had 5 errors (4 timeouts at 60 s and 1 answer the parser rejected), and fail-closed held all five.
5. This is why every decision in Steward stays in code. A model only words the brief.

Limits: a small set (50 cases), one prompt, two models and synthetic data. Jev, rewordings and the ConVerse attacks of the original plan were cut for time.

## Roadmap

| Phase                                  | Goal                                                                                                  |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 0 Setup and de-risk                    | Both agents call Nemotron and one tool through a stub Steward inside OpenShell sandboxes.             |
| 1 The two doors                        | Every model call, tool call and agent-to-agent message goes through Steward and lands in the log.     |
| 2 Rules, holds and approvals           | The Manchester evening runs live and as a replay, and every trap is held or refused.                  |
| 3 Supervisor and hosted demo           | Done: overnight watcher, morning brief, contain controls and phishing screen. Anyone with the link can try replay and playground. |
| 4 Proof (lean)                         | Done 29 Oct: code checks against a Nemotron judge (Super VL and Lightning) on the same asks and replies (Results). Jev, rewordings and the ConVerse set are cut. |

## Repository layout

| Folder                             | What goes there                                       |
| ---------------------------------- | ----------------------------------------------------- |
| `apps/gateway`                     | Steward gateway: model, tool and agent-to-agent doors, scheduler, watcher, brief, public demo server, and `judge.py` (the Nemotron judge, used only by the comparison) |
| `apps/worker`                      | Empty; the red-team runs were cut for time            |
| `apps/web`                         | Console (React, Vite, Tailwind)                       |
| `packages/core`                    | Request types, rules, checkers, flight log, watcher limits, brief facts and check, phishing screen |
| `packages/fixtures`                | Synthetic work and personal data with planted details, served as MCP tools |
| `config`                           | The policy file `steward.yaml`                        |
| `agents/openshell`                 | Sandbox policy and Steward provider profiles          |
| `agents/hermes`, `agents/openclaw` | Sandbox images and configs                            |
| `skills`                           | Agent skills                                          |
| `evals`                            | Trap suite, tasks, property tests, `walls.sh`, `ablation.py` (code against an AI check) and its results in `results/` |
| `deploy`                           | `steward.service` and `steward-forward.service` (systemd user units), `Caddyfile`, `demo/` (the `Dockerfile` and the two recordings) |
| `spikes`                           | Check scripts for the live system                     |
| `docs`                             | Notes, ADRs                                           |

## Development

Requires uv and Python 3.12.

Secrets: copy `.env.example` to `.env` and fill it in yourself. `.env` is gitignored. The policy file `config/steward.yaml` (agents, models, tools, who may ask whom) is committed and holds no secrets; it names the environment variable that holds each agent's pass.

Checks (the same as CI):

```bash
uv sync --all-packages --locked
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Run Steward (on the VM, one worker, because open agent-to-agent requests live in memory):

```bash
uv run --package steward-gateway --env-file .env uvicorn steward_gateway.app:app --host 0.0.0.0 --port 8000
```

| Route | Who | What |
| --- | --- | --- |
| `/v1/models`, `/v1/chat/completions` | agents (pass) | Model door |
| `/tools/<agent>/mcp` | agents (pass) | Tool door, including `ask` and `reply` |
| `/admin/*` | owner token | Agents, log, live log stream, chain verify, export, tool pins; the Phase 2 and 3 routes follow |
| `/admin/holds`, `/admin/holds/{hold_id}/decide` | owner token | Held calls, and approve or deny one by id and fingerprint |
| `/admin/exchanges` | owner token | Agent-to-agent asks and replies, with what was held, refused or dropped, stand-ins and change notices |
| `/admin/flags`, `/admin/clock`, `/admin/world` | owner token | Trifecta flags per agent (and clearing them), the story clock, the fixture world |
| `/admin/policy` | owner token | The policy as Steward loaded it |
| `/admin/scenario/run`, `/admin/scenario`, `/admin/replay` | owner token | Start the Manchester evening, read its state, replay a recorded session |
| `/admin/agents/{agent}/suspend`, `/admin/agents/{agent}/resume`, `/admin/agents/{agent}/revoke` | owner token | Pause an agent, resume it, revoke its pass |
| `/admin/agents/{agent}/tools/{tool}/{state}`, `/admin/controls` | owner token | Turn one tool off or on for one agent (`state` is `on` or `off`), and read every saved control |
| `/admin/brief`, `/admin/brief/run`, `/admin/brief/answer` | owner token | The latest morning brief, write one now (for a check), answer its one question |
| `/admin/mail`, `/admin/mail/{message_id}/verdict` | owner token | Mail screened as suspected phishing, and your verdict on one |
| `/admin/watcher` | owner token | The watcher's limits, each agent's pass state, pauses and baselines |
| `/a/{token}` | signed link | Approval link: GET shows a confirm page, POST decides |
| `/console` | owner | The web console, served from `apps/web/dist` |
| `/healthz` | anyone | Health check |

The public demo is a separate process (`steward demo serve`, see Public demo) with its own routes:

| Route | Who | What |
| --- | --- | --- |
| `/demo/info` | anyone | The recordings, example inputs for the playground and the limits |
| `/demo/replay` | anyone (POST) | Re-run one recording's replay on the server, one at a time, the result cached for 10 minutes |
| `/demo/check` | anyone (POST) | The playground: trim a reply, check an ask, screen an email, break an approval lock |
| `/r/<recording>/admin/...` | anyone (GET and HEAD only) | The read-only admin routes of one recording's replayed Steward. Any other method gets 403 |
| `/console`, `/healthz` | anyone | The same console build, and a health check |

The flight log is one SQLite file (`STEWARD_DB_PATH`, default `var/steward.db`).

## Console

The console is served at `/console` from `apps/web/dist`, so build it once before you open it:

```bash
cd apps/web
corepack pnpm@12.15.0 install --frozen-lockfile
corepack pnpm@12.15.0 build
cd ../..
```

The owner's Steward is not open to the internet (port 8000 is closed in the VM's security group). Reach it through an SSH tunnel, then open the console on your laptop:

```bash
ssh -i <key file> -L 8000:127.0.0.1:8000 ubuntu@<vm-ip>
```

Open http://localhost:8000/console/ and paste the owner token (`STEWARD_OWNER_TOKEN`) when it asks. The token stays in that browser tab.

| Page | What it shows |
| --- | --- |
| Today | The latest morning brief and its one question, with Approve and Deny. The watcher: each agent's state with Pause and Resume, the last pause, and anything unusual against earlier evenings. Suspected phishing, with the signs that fired and your verdict. |
| Waiting for you | Held calls as approval cards, with the reason, a Prompted by line, and the fingerprint the approval is locked to. Approve, deny or choose from the card. The watcher's pause cards wait here too. |
| Activity | The flight log as plain sentences with the outcome of each call. Decisions only by default. |
| Shared and held | Each agent-to-agent exchange: what was asked, what crossed, what was dropped, what was held or refused, plus stand-ins and change notices. |
| Agents | Each agent's pass (Pause, Resume or Revoke it), its tools and their pins with Turn off and Turn on for each, spend against budget and asks today. |
| Recorder | The hash-chain check, the export, the Manchester evening and its replay, and the story clock with a "Back to real time" button. |
| Policy | The policy as Steward loaded it. |

## Public demo

https://13-49-146-96.sslip.io shows recorded evenings of the Manchester story. Nothing there calls a model or changes anything. The demo holds no Nebius key, no agent pass and no owner token, and the owner's Steward is not reachable from it.

- Picker: two recordings, a live evening (the real agents on Nemotron 3.5, recorded on the VM) and a scripted evening (the same story with scripted actors and no model). When the demo starts, it replays each recording into its own read-only Steward in memory, so the holds, exchanges, stand-ins, brief and log chain on screen are produced by Steward's own code. The replay matched all 156 decisions of the live recording and all 86 of the scripted one.
- Start here: three steps. What the personal agent said in full (the careless answer, with Dr Shah), what crossed to the work agent (busy, can't move), and why.
- Playground: edit a reply and see what Steward trims, check an ask, screen an email (the 21:15 phishing email is there to try), or break an approval lock. Nothing is saved and no model is called.
- Re-run the replay on the server: the Recorder page replays the recording again on the server and says Identical, or names the first decision that differs. One re-run at a time, and a result is kept for 10 minutes.
- Read-only: owner buttons are disabled with the reason, and a POST to a recording's admin routes gets 403. Request bodies are capped at 16 KB (Caddy at 64 KB), and each client may send 30 POSTs a minute (a token bucket with a burst of 10). The Limits page lists what the demo does not show.
- The demo is one VM behind a free sslip.io name, and the rate limits live in memory.

To run it on the laptop (build the console first, see Console):

```bash
uv run steward demo serve --dir deploy/demo --console apps/web/dist --port 8100
```

Then open http://localhost:8100/. The recordings in `deploy/demo` come from `steward demo record-scripted` (the scripted evening) and `steward demo bundle` (a flight-log export, after a scan that refuses it if a pass, a key or a token from the environment appears in it).

How it is hosted: on the VM the demo is a Docker container (`steward-demo`) with a read-only root, all capabilities dropped and no-new-privileges, listening on 127.0.0.1:8100 only. Caddy sits in front on ports 80 and 443. It gets and renews a Let's Encrypt certificate for the name, redirects http to https, adds the security headers and forwards to the container. Port 8000, where the owner's Steward listens, stays closed, and you reach that Steward through the SSH tunnel in the Console section.

## Run as a service

On the VM, run Steward as a systemd user service instead of a tmux session. If a tmux session still runs Steward, stop it first (`tmux kill-session -t steward`), because both would want port 8000. Then, from the repository root:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/steward.service ~/.config/systemd/user/steward.service
systemctl --user daemon-reload && systemctl --user enable --now steward
curl -s http://127.0.0.1:8000/healthz
```

The unit expects the repository at `~/StewardAI` and uv at `~/.local/bin/uv`. It runs the same uvicorn command as above with `.env`, restarts on failure and starts at boot (`loginctl enable-linger` keeps it running after you log out). It runs one worker only. Follow the logs with `journalctl --user -u steward -f`.

The port forward to the work agent (127.0.0.1:8642, how Steward reaches Hermes) is its own unit, so it comes back after a reboot or a VM stop:

```bash
cp deploy/steward-forward.service ~/.config/systemd/user/steward-forward.service
systemctl --user daemon-reload && systemctl --user enable --now steward-forward
```

It runs `openshell forward start 8642 work-agent` in the foreground, and systemd restarts it every 10 seconds if it stops, also while the gateway or the sandbox is still coming up after a boot. `openshell forward list` shows nothing for a foreground forward, which is expected.

The demo container, after the console is built. From the repository root:

```bash
docker build -f deploy/demo/Dockerfile -t steward-demo:local .
docker rm -f steward-demo
docker run -d --name steward-demo --restart unless-stopped --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges -p 127.0.0.1:8100:8100 steward-demo:local
```

Caddy, installed once from its official apt repository:

```bash
sudo apt-get install -y gpg curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update
sudo apt-get install -y caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

Open ports 80 and 443 in the VM's security group for Caddy (port 80 serves the Let's Encrypt challenge and the redirect to https), and keep port 8000 closed. Until they are open, Caddy cannot get the certificate and retries by itself. The Caddyfile names `13-49-146-96.sslip.io`, so change that name if the VM's address changes.

## Play and replay the evening

The Manchester evening (Sun 11 Oct 19:00 to Mon 12 Oct 07:35, see the [demo story](docs/demo-story.md)) runs as a scenario inside Steward. Live mode plays it against the real agents. Replay plays a recorded session back through a fresh Steward, with no sandbox, no model call and no key.

```bash
uv run --env-file .env steward scenario manchester --mode live --approvals scripted --speed 60
```

Live mode needs Steward running with both agent sandboxes up, and `STEWARD_OWNER_TOKEN` in `.env`. The command starts the run (`POST /admin/scenario/run`), then follows the flight log and prints each beat and decision until the run ends.

- `--approvals scripted` approves for you at 19:06 and 07:35. Use `--approvals manual` to decide in the console yourself.
- `--speed 60` fast-forwards the quiet gaps between beats, with at most 2 minutes of real waiting per gap. While an agent works, the clock runs at real speed.
- `--real-time` plays the evening at the clock's own pace (12 hours 35 minutes) with no fast-forward, so the 07:30 brief arrives at 07:30. `--speed` is ignored.
- `--approvals owner-first --owner-wait-min N` lets you decide the 19:06 and 07:35 holds in the console or Telegram first. Whatever is still held after N minutes (default 15, above 0 and at most 720) gets the scripted decision, and the wait ends early once everything is decided.
- `--demo-night` keeps the story's own times but paces each beat to a fixed London wall time: 19:06 at 19:40, 21:15 at 21:45, 22:40 at 23:45, 23:30 at 00:30, 00:50 at 01:30, 01:30 at 02:30, the 07:30 brief at 03:30 and the 07:35 decision at 03:35. Between two beats the story clock runs at the rate that lands it on time, and a beat whose time has already passed (by under six hours) goes at once. Run it as `--mode live --demo-night --approvals manual`: the plan waits 90 minutes for you at 19:06 and 210 minutes at 07:35 (`--owner-wait-min` does not apply), and nothing is decided for you.
- `--attacks` decides who plays the 22:40 attack. `fallback` (the default) lets Hermes try first and plays a scripted tricked agent if Hermes does not ask for the reasons. `agent` leaves it to Hermes alone. `scripted` always plays it. The phishing screen removes the hidden instruction before the agent reads the 22:40 email, and in the two live runs made since the screen was added Hermes reported the email, so the scripted tricked agent played the probing asks.
- Scripted actors are marked "scripted" in the log. Besides the 22:40 attack, they play the 00:50 and 01:30 to-do writes.
- After a successful run, story time keeps running from Mon 07:35 so held calls stay open to decide. The Recorder page has "Back to real time".

```bash
uv run --env-file .env steward scenario manchester --mode replay --from-server --recording var/steward-export.jsonl
```

Replay downloads the flight-log export from the running Steward, replays the last scenario session in memory and prints PASS with the number of decisions that matched, or FAIL at the first one that differs. The exit code is 0 on a match, and the result is written to `var/replay-result.json`. With an export already on disk, no server is needed:

```bash
uv run steward scenario manchester --mode replay --recording var/steward-export.jsonl
```

A pass shows that Steward's decisions are deterministic. It does not show that the agents would act the same way again. The bundled scripted evening replays with 86 of 86 decisions matching, and the bundled live evening (run 10) with 156 of 156.

## Trap suite

```bash
uv run pytest evals
```

`evals/` holds 49 traps (`evals/traps/*.yaml`) and 13 normal tasks (`evals/tasks/*.yaml`). Each file is a list of steps played through a real Steward in process, the way the agents and the owner would use it. A trap passes when it ends refused, held, paused or with the detail dropped. A task passes when normal work still gets done, for example a train booked before any untrusted content has been read, or the brief's one question answered and the paid changes run once.

The traps fall into twelve categories: reasons and free text (5), planted details in replies (6), limits and addressing (4), address (4), trifecta (5), tools (5), writes (5), budget (3), bypass (3), watcher and contain (6), phishing (2) and public demo (1). The three newest categories hold a runaway loop and a refusal storm that pause the agent, a paused agent refused at every door, a pause that survives a restart, a tool that is off, a revoked pass, a phishing link that is refused, hidden text that is removed, and a visitor who cannot change the demo. The 46 traps that need no sandbox run on the laptop and in CI, together with the tasks, the property tests (300 Hypothesis examples each for travel-limit and needs replies, next to the availability one from Phase 1) and a check that the suite really has 49 traps and 13 tasks.

The 3 bypass traps need the real sandboxes, so they run on the VM:

```bash
bash evals/walls.sh
```

It prints PASS or FAIL for each check and exits non-zero if a wall did not hold. The checks: neither sandbox can call Token Factory directly, `curl` inside the work sandbox cannot use the pass against Steward, and neither sandbox can read Steward's `.env`. It also confirms that each sandbox holds a placeholder pass, not the real one.

## Docs

- [Demo story: the Manchester trip](docs/demo-story.md)
- [Features](docs/features.md)
- [Questions and doubts](docs/questions-and-doubts.md)
- [Feedback log](FEEDBACK.md)

## Video
- [Youtube Demo](https://www.youtube.com/watch?v=UrlHmv_-PrM)

## Licence

Apache-2.0, see [LICENSE](LICENSE).
