# Challenges

This file lists the main problems hit in each phase and how they were solved.

## Phase 0

### Laptop too small for the agents

Problem: The laptop has about 7 GB of memory and cannot run Docker (a tool that runs programs in isolated boxes). The agents and their sandboxes (the isolated boxes they run in) need an always-on Linux machine.

How we approached: Rented one AWS virtual machine (4 CPUs, 16 GB of memory) and kept all agent and sandbox work there. The laptop is used for code, tests and docs.

Result: Everything agent-related runs on the VM. The VM must be stopped when not in use to save money.

### Could not log in to the VM

Problem: Remote login (SSH) kept timing out, three times. The firewall only allowed one internet address, and the address changed between university and home. Windows also refused the key file because its permissions were too open.

How we approached: Added the current address to the firewall rule each time and locked down the key file's permissions.

Result: Login works. The rule needs updating when working from a new place.

### Pushes to GitHub failed

Problem: The computer had two GitHub accounts saved and picked the one without write access. One terminal also could not show a login prompt.

How we approached: Pushed from Git Bash and named the right account in the push command.

Result: Pushes work every time.

### Work agent image would not run

Problem: The official image for Hermes (the work agent) starts as the administrator (root). OpenShell, the tool that creates the sandboxes, refuses images that run as root.

How we approached: Built our own small image that installs Hermes as a normal user.

Result: Hermes runs inside its sandbox.

### First model key rejected

Problem: The first Nebius key returned "unauthorised". Nebius Token Factory is the service that runs the models.

How we approached: Dharan created a new key and put it in the VM's secrets file himself.

Result: All model checks passed with both Nemotron models (the AI models used in this project).

### Some models had been retired

Problem: Token Factory removed several models on 31 August, so the planned model names might not exist.

How we approached: Read the live model list and confirmed each name before using it.

Result: Nemotron 3 Super (work agent) and Nemotron 3 Nano (personal agent) are both available.

### Sandbox commands hung scripts

Problem: One OpenShell command swallowed the rest of the script, and another never let the script finish.

How we approached: Gave the first command no input and sent the second command's output to a file.

Result: The setup scripts run start to finish. This is written up in the feedback log.

### Personal agent could not save data

Problem: OpenClaw (the personal agent) keeps a small database that needs a temporary folder, and the sandbox did not allow it. Telling OpenClaw to use another folder had no effect, because the sandbox drops that setting.

How we approached: Allowed that one temporary folder in the sandbox rules.

Result: OpenClaw runs normally.

### Personal agent could not find its tools

Problem: OpenClaw hid its tools behind a search step. The small model kept calling a tool name that did not exist, then said it had no calendar access (3 runs out of 3).

How we approached: Switched the search step off so the tools are listed directly.

Result: 3 runs out of 3 worked, but each request became very large (about 31,000 tokens, the units a model counts and charges for). Phase 1 fixed that.

### Two models report reasoning differently

Problem: The bigger model puts its reasoning in two places in its reply and the smaller one in only one. A program that reads the wrong place loses the reasoning.

How we approached: Checked with a real test request instead of assuming.

Result: Steward (the gatekeeper this project builds) passes answers through unchanged, so nothing breaks. This is written up in the feedback log.

## Phase 1

### Less time than planned

Problem: Phase 0 ran long, which left about six days for Phases 1 to 4.

How we approached: Squeezed the schedule (Phase 1 in one weekend). Chose SQLite, a database that lives in a single file, instead of a database server, because the laptop cannot run Docker and the tests then need no extra service.

Result: Phase 1 was built and tested in one day. The database can be swapped later.

### Proving the log was not edited

Problem: The log of everything the agents did has to show if anyone changed it afterwards.

How we approached: Each entry stores a fingerprint (a hash) of the entry before it, so changing one entry breaks the chain from that point on. The database also refuses edits and deletions.

Result: On the VM, edits and deletions were refused. A copy of the file with one entry changed by hand was caught at exactly that entry (entry 20).

### Messy answers from the personal agent

Problem: The small model adds extras (event titles, places, notes, ids) and words things differently each time.

How we approached: Steward accepts the answer, keeps only date, start, end, busy and can-move, rounds times out to the half hour, and records what it removed.

Result: In the first live run Steward removed the titles and places. In the second the model sent none. Either way only "busy, can't move" style blocks crossed. Example: the 14:15 to 15:45 hospital appointment crossed as "Wed 14 Oct, 14:00 to 16:00, busy, can't move".

### Requests were huge

Problem: Every model call carried about 13,000 tokens (work agent) and 31,000 tokens (personal agent), mostly descriptions of built-in tools the agents never use. That is slow and costs money.

How we approached: Turned off every built-in tool so each agent only sees Steward's tools.

Result: About 3,000 to 5,000 tokens per call for both agents. A full ask costs under one cent.

### Updating sandbox rules failed

Problem: OpenShell's checker said the rule set "already exists", and the update command then asked for a hidden version number. Both failed.

How we approached: Exported the current rules to read the version number and sent it with the update.

Result: The update worked. The work agent's key now only works for Hermes's own program. This is written up in the feedback log.

### Work agent lost its connection

Problem: When Steward restarted, Hermes's first tool call failed with "session expired". Connections also expired after 30 quiet minutes by default.

How we approached: Kept quiet connections open for 24 hours, and checked that Hermes reconnects and retries by itself.

Result: The 30-minute drop is gone. After a Steward restart Hermes still fails once and then retries, and the log showed the request was counted only once.

### Personal agent is slow

Problem: Each answer takes 40 to 90 seconds, because OpenClaw starts a fresh run every time.

How we approached: Steward waits up to 150 seconds, stops waiting early if the agent finishes without answering, and shares nothing if no answer comes.

Result: Both live runs answered in time. A replay will be used for the demo video later.

### Keeping secrets out of the repo

Problem: The laptop's example settings file had real-looking passwords typed into it, and the repo is public.

How we approached: Committed a clean copy of the file without touching the local one, and checked the pushed files for secret-looking values.

Result: Nothing secret was pushed. Dharan was told to clean the local copy.

### Garbled symbols in the console

Problem: A script on Windows re-saved two console files in the wrong text encoding, so dots and ellipses showed as strange characters.

How we approached: Found the bad characters and rewrote them. Files are now saved explicitly as UTF-8, a standard text encoding.

Result: The console displays correctly.

### Live log needed the sign-in token

Problem: The browser's built-in way to receive live updates cannot send a password with the request.

How we approached: The console reads the live stream with an ordinary web request, which can carry the token.

Result: New log entries appear in the console within a second, without reloading.

### A model call was refused

Problem: Token Factory returned "overloaded" for one call during a live run.

How we approached: Hermes retried by itself, and Steward recorded the failed call in the log.

Result: The run completed. The failure is visible in the log.

## Phase 2

### A lot to build in little time

Problem: The plan gave two days (Mon 26 and Tue 27 Oct) for a long list: new request types, holds, approvals, rules, web search, a scenario runner, replay, 40 traps (test cases that try to trick Steward) and a new console.

How we approached: Settled the ten open design decisions in one round of questions before starting (every recommendation was accepted). Then built in dependency order: core rules, the demo's pretend world, Steward's doors, the evening runner and replay, the traps, and last the console, with tests at each step.

Result: Built and tested on Sunday 25 Oct, a day ahead of the plan.

### The trust rule would have blocked the story

Problem: Steward holds any call that could send data out once an agent has read private data and also seen untrusted content (the trifecta rule). The demo data marks the client's real email as "external", so reading it at 19:00 would have counted as untrusted content and held the 19:03 web page read.

How we approached: The policy now lists trusted mail domains (the company's and the client's real one) instead of using the label in the data. The lookalike domain stays untrusted.

Result: The page read goes through, and everything that could send data out after it waits for the owner.

### Making replay possible at all

Problem: A replay plays a recorded evening back through Steward and checks that every decision comes out the same. That could never match exactly, because ids were random and the rules used the real time.

How we approached: One clock for every rule. Ids made from the session and a counter. Every agent call logged once with its arguments. Results from outside (tool results, an agent finishing a run) held back until the replay reaches them.

Result: The whole evening replays with no model, no key and no sandbox. In the test evening 75 of 75 decisions match in about 5 seconds.

### Replay drifted by one millisecond on the VM

Problem: The demo world stamped a to-do change with the live clock a moment after Steward decided, but the replay stamped it when the result was logged. The two times differed by one millisecond. It passed on the laptop and failed on the slower VM.

How we approached: While a call runs, the demo world's clock is pinned to the moment Steward decided it (or the moment of the owner's approval), and calls to the tools run one at a time. Replay stops at the first difference. A test slows every tool call by 5 milliseconds to keep this honest.

Result: Replay matches on both machines. With the old behaviour, the test finds the difference at decision 68, in under 5 seconds.

### Story time versus real time

Problem: After the evening finished, Steward went back to the real clock, two weeks after the story, so everything still waiting for approval expired at once.

How we approached: Story time keeps running after a run, and the Recorder page has a "Back to real time" button.

Result: The 01:30 stale write (a change to the shared to-do list made from an out-of-date copy) is still waiting in the console after the run.

### Where to put the poisoned page

Problem: The demo needs a web page with a hidden instruction, read through Tavily (a service that searches the web and reads pages for agents). Tavily cannot read .example addresses (reserved fake domains) or the VM, which is closed to the internet.

How we approached: Published the fictional page on GitHub Pages (free web hosting from the repo), and first tested which hidden text Tavily passes on.

Result: Text hidden with display:none, four other CSS tricks and the aria-hidden attribute came back to the agent (CSS is the code that styles a web page). HTML comments and noscript blocks did not. The page uses display:none.

### The address could leak back through results

Problem: The taxi company (a pretend one in the demo) repeats the pickup address in the booking confirmation and in the list of bookings, so the real address could still reach the work agent.

How we approached: Steward replaces the home address with the stand-in (the placeholder [ADDR-1], swapped for the real address only when a booking goes out) in every result the work agent receives.

Result: The work agent only ever sees [ADDR-1]. A test checks every result it received.

### Approving the address had to cover the taxi booking

Problem: After the poisoned page, the trifecta rule holds every booking, so the taxi would have needed a second tap after the address approval.

How we approached: The "Taxi booking only" approval covers exactly one taxi booking for that trip, within the trip limit. Anything else that uses the stand-in is refused.

Result: One tap approves the address and the one taxi booking that goes with it. In live runs 3 to 5, Hermes booked the taxi with [ADDR-1] right after the approval, with no second tap, and the real address went only into the booking.

### The real work agent does not always fall for the attack

Problem: Hermes, the work agent, has instructions saying that text in tool output is not an instruction, so it often ignores the poisoned email. Then the refusal and the held questions would never show in the demo.

How we approached: If Hermes does not ask for the reasons behind the busy days, a scripted "tricked agent" plays that part through the same door and is marked "scripted" in the log.

Result: The refusal and the held probing questions happened in every live run.

### The live evening drifted from the story

Problem: Hermes does not always follow the story. In the first live run it looked for trains from London on the web (the trifecta rule held that search) and never booked anything, so the 19:06 approvals had nothing to approve. In runs 4 and 5 it booked the 11:30 train instead of the story's 09:30. That train does not clash with the blood test, so the 23:30 change notice needed no move and there was no paid change to approve at 07:35.

How we approached: After run 1 we told Hermes where the traveller lives (Exeter St Davids) and to use the travel tools, not web search. After run 4 we told it to pick the earliest train that fits the travel limits and the client's window, which is the 09:30 in the story. Run 5 still chose the 11:30, which is reasonable: the school run keeps the owner busy until 09:00, so a 09:00 taxi pickup is tight. We left that choice alone, so the scripted evening shows the paid change instead.

Result: Run 2 made no booking either: its first availability ask had timed out and three day-by-day asks were held as a burst (see the timeout item below). Runs 3 to 5 ran the 19:00 to 19:06 part of the story live: the typed asks came back trimmed, Hermes read the poisoned page through Tavily, the train booking was held by the trifecta rule and then approved and booked once, the address card was answered, Steward issued [ADDR-1], and the taxi was booked with the stand-in. Run 3 took the 09:30 but did not move it after the notice, and runs 4 and 5 took the 11:30. So the paid change at 07:35 never came up live. The scripted evening test (a test that plays the whole story with scripted agents instead of models) shows it: change held, approved, executed once, the agent told. The approved-train-change task and the hold tests cover the same path.

### Console readability

Problem: The first console used tiny grey text and showed log codes, which were hard to read.

How we approached: A typeface made for legibility (Atkinson Hyperlegible) at 16 pixels, sentences instead of codes, colour that carries meaning (held, refused, trimmed), and approvals drawn as pass-style cards that show the fingerprint (a short code that ties an approval to the exact request).

Result: Checked in a browser, in light and dark mode, against a local Steward with a played evening. Approving from a card works.

### The personal agent ran out of time on one answer

Problem: In live run 2, OpenClaw (the personal agent) used up its whole 120 second turn on the first availability question (it read the calendar once, then hit the limit). No answer came back, so nothing was shared.

How we approached: Gave OpenClaw 200 seconds per turn and made Steward wait 240 seconds for an answer. Both stay under the 300 seconds after which Hermes gives up on a tool call.

Result: In run 3 all three answers (availability, travel limits, needs) came back in time, each in under a minute.

### Steward's own wording could mislead an agent

Problem: Steward's messages are read by a model, so loose wording can send it the wrong way. For an approved address card, the status said the call was "done once", which could be read as "the taxi is booked". The 23:30 change notice also did not name the tools that move a booking, and in run 3 Hermes asked for an address again but left the clashing 09:30 train alone.

How we approached: Made the status explicit ("Approved. Nothing is booked yet: book the taxi now with travel_book_taxi, using [ADDR-1] as the address."). The change notice now names travel_list_bookings and travel_change_booking.

Result: Hermes booked the taxi in run 3 despite the old wording, so the new wording is a precaution, and it booked the taxi in runs 4 and 5 too. After the notice in run 5, Hermes listed its bookings (a tool the notice now names) and left them alone, correctly, because the 11:30 train and the 10:45 pickup do not clash. No live run has had a real clash since the change, so moving a booking after a notice has not been seen live.

### The command-line summary hid allowed calls

Problem: In live run 3 the taxi booking looked missing. The steward command (which starts a run and prints a summary of it) listed held and refused calls but not the calls Steward allowed, so a working run looked broken.

How we approached: Checked the flight log (Steward's record of every call). It showed Hermes had booked the taxi and Steward had allowed the call because the address approval covered it. Then changed the command to print allowed calls too.

Result: The run was fine, and the summary now matches the log. Run 5 was the first run with the command printing every call.

## Phase 3

### Two models were announced for removal before judging

Problem: Token Factory announced that the two models behind the agents, nvidia/nemotron-3-super-120b-a12b and nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B, leave Serverless on 9 Nov 2026. The notice gave 12 days and there is no automatic reroute, so without a change the agents would have had no model before judging (1 to 15 Dec).

How we approached: Ran a spike on the VM on 28 Oct against the recommended replacements, nvidia/Nemotron-3.5-Super-VL and nvidia/Nemotron-3_5-Lightning, with a plain call, streaming, tools and streaming with tools. Then set the work agent to Super VL and the personal agent and the brief to Lightning, rebuilt both agent images and recreated both sandboxes. The old models' prices stay in the policy, so recordings made with them meter the same in replay.

Result: Both models passed all four checks, at the same prices as the models they replace. Live runs 6 and 7 ran on them. The details are in the update to ADR 0011.

### The brief check rejected the model's correct text

Problem: In live run 6 the model's brief was rejected with "contains a private detail", and the brief fell back to the template. The check forbids the planted strings, and one of them is the client's name, Brightwater. The 22:40 lookalike sender's domain (brightwater-logistlcs.example) contains it, and the model had only copied that domain from the facts.

How we approached: A planted string that the facts themselves carry is no longer forbidden in the model's lines. Copying a fact is not a leak. Anything else from the list still is.

Result: In run 7 the brief was written by Nemotron 3.5 Lightning and passed the check.

### Text written by others was reaching the brief writer

Problem: The facts handed to the model carried an email's subject and sender domain, a to-do item's title and the name of the request type a refused ask had used. A sender or an agent wrote each of those, so attacker text could reach the model that words the brief. The design says the facts are typed values only.

How we approached: Those four strings were cut from what the model sees (the sender domain last, after a review of the docs pointed out that the sender chooses it). The template, which code writes, still quotes them. The prompt now maps each fact to a topic line, forbids counting, asks for one topic per line and for spend in dollars.

Result: No text written by a sender or an agent reaches the model. The model's lines are still checked against the facts afterwards.

### The model put a count on the wrong label

Problem: In the Telegram tests the brief writer described a held call as a held ask. The counts in its input had short names that could be read either way. In run 7 the brief also copied a key name, "asks_held 6", and left out the Spend line. The check could not catch that wording.

How we approached: Each count now has a name that says what it counts (calls_held_for_owner, asks_held_by_daily_limit_or_burst, requests_answered, calls_refused). A copied field name now fails the check and sends the brief to the template.

Result: After the change, 5 of 6 attempts passed the check, on run 6's facts and on a quiet live night, and 1 was rejected for a field name and fell back to the template. The bundled live recording was made before the change, so its brief still reads "asks_held 6" and has no Spend line. A replay keeps the recorded text.

### A dollar amount was read as a clock time

Problem: The check reads a number such as 07:30 or 9.30 as a clock time that must appear in the facts. An amount like $0.00 matched that pattern, so a correct Spend line could be rejected.

How we approached: A number that follows a $ or £ sign is no longer read as a time.

Result: Spend lines with dollar amounts pass the check.

### The public demo had to be a replay

Problem: A demo with a model behind it would need a Nebius key and an owner's Steward open to the internet. The demo had to show the real console, with holds, exchanges and the brief, and still hold no key, no pass and no owner token.

How we approached: The demo is literally a replay. `steward demo serve` replays each bundled recording into its own in-memory Steward, one replayed Steward per recording, and the console reads it through read-only routes. Replay copies the story's beats into the replayed log (they are not compared), so the demo shows the beats and its approval cards say what prompted them.

Result: The Recorder page's "Re-run the replay on the server" answers "Identical: 156 decisions matched" for the live recording (run 8; it read 158 for run 7), and a POST to an owner route gets 403.

### The Telegram bot token reached the service log

Problem: With Telegram on, the VM's journal logged the bot token on every Bot API request. The Bot API puts the token in the request URL, and httpx logged each URL at INFO. It was found in the first test with a real bot.

How we approached: The httpx logger is set to WARNING in the Telegram code, and a test checks it.

Result: The token no longer reaches the log. A token that was logged earlier counts as exposed, so it has to be revoked in BotFather and replaced in the VM's .env.

### The owner was asked about one held call twice

Problem: A call held at night got a Telegram card, and the 07:30 brief then asked the same question again, so the owner was asked twice about the same call.

How we approached: Quiet hours. From 22:00 (`brief.quiet_from`) until the brief, a held call sends no card, and the brief asks, with Approve and Deny buttons. If a call in the brief's question already has an open card, the brief says "Answer it on the card sent at HH:MM" and has no buttons. Once every call in a brief's question is decided anywhere, the brief is edited to say Answered and loses its buttons.

Result: In a live Telegram check no card arrived in quiet hours, Deny on the brief decided the hold, and the brief then showed Answered.

### The Hermes forward died whenever the VM stopped

Problem: The OpenShell port forward to Hermes's API (127.0.0.1:8642) was started by hand and died whenever the VM stopped. Steward reaches the work agent through it, to tell it about change notices and decided holds.

How we approached: The forward is now a systemd user unit, `steward-forward`. It runs in the foreground, and systemd restarts it every 10 seconds if it stops, including while the gateway or the sandbox is still coming up after a boot.

Result: After `sudo systemctl reboot` the forward was active and Hermes answered its health check with 200, with no manual step.

### Trusting X-Forwarded-For behind Docker

Problem: The demo's rate limit needs the visitor's address. Caddy passes it in X-Forwarded-For, but a client can send that header too, so it can be trusted only when the request comes from the proxy. Inside Docker the proxy's connection arrives from the bridge network, not from loopback. Python also counts documentation addresses as private, so a plain "is private" test trusts more than the proxy.

How we approached: The trusted networks are listed explicitly (127.0.0.0/8, ::1/128, 10.0.0.0/8, 172.16.0.0/12 and 192.168.0.0/16). The last X-Forwarded-For entry, the one Caddy sets, is used only when the connection comes from one of them.

Result: Through Caddy, 12 quick POSTs to /demo/check gave 11 answers with status 200 and then 429 (a burst of 10 plus a small refill).

### A call held at 19:00 expires before the 07:30 brief

Problem: A hold expires after 12 hours, so a call held at 19:00 is gone at 07:00 and the 07:30 brief never asks about it.

How we approached: Left as it is for now. The story's paid change is held at 23:30, so it is still open at 07:30, and the brief asks only about calls still open.

Result: A known limitation. A call held early in the evening has to be answered before it expires.
