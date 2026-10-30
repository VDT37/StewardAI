# Setup guide

How to run Steward, the console, the Manchester evening, the trap suite and the public demo. The quick no-keys path is in the [README](../README.md#run-it-yourself-about-5-minutes-no-api-keys); this guide covers the full live system, which needs a Linux VM, NVIDIA OpenShell and your own keys.

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

The Manchester evening (Sun 11 Oct 19:00 to Mon 12 Oct 07:35, see the [demo story](demo-story.md)) runs as a scenario inside Steward. Live mode plays it against the real agents. Replay plays a recorded session back through a fresh Steward, with no sandbox, no model call and no key.

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
