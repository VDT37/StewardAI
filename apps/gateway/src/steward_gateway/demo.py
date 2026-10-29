"""The public demo (Phase 3 Part F, P3-D3 A and P3-D5 A): recorded evenings, read-only, and a
playground.

`steward demo serve` replays each bundled recording (deploy/demo/recordings.json) into its own
in-memory Steward in demo mode and keeps it running, its clock frozen at the last recorded
event. What a visitor sees is therefore the replay itself: holds, exchanges, stand-ins, the brief
and the chain all come from Steward's own code. The console reads recording NAME at
/r/NAME/admin/..., GET only, one recording at a time.

A visitor can also re-run the replay of a bundled recording on the server (POST /demo/replay,
one at a time, the result cached for 10 minutes) and try Steward's checks in the playground
(POST /demo/check): trim a reply, check an ask, screen an email, break an approval lock. Nothing
a visitor does calls a model or changes a Steward: the demo holds no key, no pass and no owner
token. Request bodies are capped at 16 KB, and each client address may make 30 POSTs a minute.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError

from steward_core.checker import Ask, CheckContext, Decision, make_checker
from steward_core.phishing import screen_message, withhold
from steward_core.request_types import REQUEST_TYPES, request_message
from steward_fixtures import load_raw, planted_details
from steward_fixtures.servers import demo_window
from steward_gateway.holds import hold_fingerprint, short_fingerprint
from steward_gateway.replay import (
    ReplayRun,
    load_recording,
    pick_session,
    replay_events,
    replayed,
)
from steward_gateway.scenario import TRICKED_REASONS
from steward_gateway.scripted import TRIP

log = logging.getLogger("steward.demo")

DEMO_DIR = Path("deploy/demo")
MANIFEST = "recordings.json"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_BODY = 16 * 1024
RATE_PER_MINUTE = 30
RATE_BURST = 10
REPLAY_CACHE_S = 600.0
# Where Caddy's requests come from: loopback, or a private network (Docker's bridge).
PROXY_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in ("127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
# Strings that must never be in a bundled recording (pass prefix, Tavily key prefix, a bearer
# header), besides the values of the secrets in the environment.
SECRET_MARKERS = ("stw_", "tvly-", "Bearer ")
SECRET_ENV = (
    "NEBIUS_API_KEY",
    "STEWARD_OWNER_TOKEN",
    "TAVILY_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "HERMES_API_KEY",
    "STEWARD_APPROVAL_SECRET",
)

Kind = Literal["live", "scripted"]


# Recordings --------------------------------------------------------------------------------------


@dataclass
class Recording:
    name: str
    label: str
    kind: str
    file: Path
    events: list[dict[str, Any]]
    run: ReplayRun | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    rerun: dict[str, Any] | None = None
    rerun_at: float = 0.0


def load_manifest(folder: Path) -> list[Recording]:
    """The bundled recordings, in manifest order. A missing manifest means none."""
    path = folder / MANIFEST
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    found = []
    for entry in data.get("recordings", []):
        name = str(entry["name"])
        if not NAME.match(name):
            raise ValueError(f"bad recording name {name!r}")
        file = folder / str(entry["file"])
        events = pick_session(load_recording(file), entry.get("session_id"))
        found.append(
            Recording(
                name=name,
                label=str(entry.get("label") or name),
                kind=str(entry.get("kind") or "live"),
                file=file,
                events=events,
            )
        )
    return found


def secret_problems(text: str, env: Mapping[str, str]) -> list[str]:
    """Why a recording may not be published; never quotes the secret itself."""
    problems = [f"contains the marker {marker!r}" for marker in SECRET_MARKERS if marker in text]
    names = [n for n in env if n in SECRET_ENV or n.startswith("STEWARD_PASS_")]
    for name in sorted(names):
        value = env.get(name, "").strip()
        if len(value) >= 8 and value in text:
            problems.append(f"contains the value of {name}")
    return problems


def summarise(rec: Recording) -> dict[str, Any]:
    """What the console shows about a recording before any page loads: who used which model,
    the story's beats and the result of the replay that built the demo."""
    events = rec.events
    models: dict[str, set[str]] = {}
    for e in events:
        if e["kind"] == "model.call" and e.get("agent"):
            models.setdefault(str(e["agent"]), set()).add(str(e["payload"].get("model")))
    written = next((e for e in reversed(events) if e["kind"] == "brief.written"), None)
    start = next((e for e in events if e["kind"] == "scenario.start"), None)
    policy = (start or {}).get("payload", {}).get("policy") or {}
    session = str(events[0]["session_id"])
    stamp = re.match(r"^s-(\d{4})(\d{2})(\d{2})-", session)
    return {
        "name": rec.name,
        "label": rec.label,
        "kind": rec.kind,
        "session_id": session,
        "recorded_on": "-".join(stamp.groups()) if stamp else None,
        "events": len(events),
        "models_used": {agent: sorted(names) for agent, names in sorted(models.items())},
        "models_set": {
            **{a: p.get("model") for a, p in sorted((policy.get("agents") or {}).items())},
            "brief": (policy.get("helper") or {}).get("model"),
        },
        "brief": {
            "source": written["payload"].get("source"),
            "model": written["payload"].get("model"),
            "problems": written["payload"].get("problems"),
        }
        if written
        else None,
        "beats": [
            {"ts": e["ts"], "beat": e["payload"].get("beat"), "actor": e["payload"].get("actor")}
            for e in events
            if e["kind"] == "scenario.beat" and not str(e["payload"].get("beat")).endswith(")")
        ],
        "replay": rec.run.result if rec.run else None,
    }


# Playground --------------------------------------------------------------------------------------


class CheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["reply", "ask", "mail", "lock"]
    recording: str | None = None
    # reply: the ask's fields and the answering agent's full reply; ask: the ask's fields.
    request_type: str | None = None
    request: dict[str, Any] | None = None
    reply: Any = None
    fields: Any = None
    # mail: from, subject, body, body_html.
    message: dict[str, Any] | None = None
    # lock: the held call as the owner saw it, and the call as it would run.
    hold: dict[str, Any] | None = None
    edited_args: Any = None


def _planted(value: Any) -> list[str]:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    lowered = text.lower()
    found = []
    for detail in planted_details("personal"):
        if detail["text"].lower() in lowered and detail["text"] not in found:
            found.append(detail["text"])
    return found


async def check(body: CheckIn, run: ReplayRun) -> dict[str, Any]:
    """Run Steward's own checks on what a visitor typed. Pure: nothing is stored or sent."""
    rt = run.runtime
    policy = rt.settings.policy
    route = policy.route("work", "personal")
    window = demo_window(rt.world.anchor)
    checker = make_checker(policy.checker, policy.checker_timeout_s)
    context = CheckContext("work", "personal", route, 1, window, 1)

    if body.kind == "mail":
        message = {k: v for k, v in (body.message or {}).items() if isinstance(v, str)}
        message.setdefault("id", "playground")
        screen = screen_message(message, policy.trusted_mail_domains)
        return {
            "kind": "mail",
            **screen.as_dict(),
            "agent_reads": withhold(message, screen) if screen.suspected else message,
        }

    if body.kind == "lock":
        hold = body.hold or {}
        agent, tool = str(hold.get("agent")), str(hold.get("tool"))
        hold_id = str(hold.get("hold_id"))
        seen = hold_fingerprint(agent, tool, hold.get("args"), hold_id)
        runs = hold_fingerprint(agent, tool, body.edited_args, hold_id)
        return {
            "kind": "lock",
            "approved": short_fingerprint(seen),
            "would_run": short_fingerprint(runs),
            "approved_full": seen,
            "would_run_full": runs,
            "matches": seen == runs,
            "outcome": "the approval stands: the call is exactly what you saw"
            if seen == runs
            else "the approval is void: the call changed after you saw it",
        }

    request_type = str(body.request_type or "")
    fields = body.fields if body.kind == "ask" else (body.request or {})
    asked = await checker.check_request(context, Ask("personal", request_type, fields))
    verdict = asked.verdict
    request_out = {
        "decision": verdict.decision.value,
        "rule": verdict.rule,
        "reason": verdict.reason,
    }
    if body.kind == "ask":
        out: dict[str, Any] = {"kind": "ask", **request_out}
        rtype = REQUEST_TYPES.get(request_type)
        if asked.request is not None and rtype is not None and rtype.reply_model is not None:
            # What the personal agent would read: a fixed template and the typed fields only.
            out["message_to_personal"] = request_message(
                rtype,
                "r-playground",
                "work",
                asked.request,
                read_tool=rtype.read_tool,
                reply_tool="reply",
            )
        return out

    if verdict.decision is Decision.REFUSE or asked.request is None:
        return {"kind": "reply", "request": request_out, "decision": "refuse", "crossed": None}
    replied = await checker.check_reply(context, request_type, asked.request, body.reply)
    crossed = replied.reply.model_dump(mode="json") if replied.reply is not None else None
    describe = REQUEST_TYPES[request_type].describe
    reads = (
        describe(asked.request, replied.reply) if describe and replied.reply is not None else None
    )
    return {
        "kind": "reply",
        "request": request_out,
        "decision": replied.verdict.decision.value,
        "rule": replied.verdict.rule,
        "reason": replied.verdict.reason,
        "crossed": crossed,
        "asker_reads": reads,
        "dropped_fields": replied.dropped_fields,
        "rules_fired": replied.rules_fired,
        "detail_minutes": route.detail_minutes if route else None,
        "planted_in_reply": _planted(body.reply),
        "planted_crossed": _planted([crossed, reads]),
    }


def examples(recordings: list[Recording]) -> dict[str, Any]:
    """Starting points for the playground, taken from the recordings: the personal agent's own
    replies (the most revealing one of each type first), a held call, the attack asks and the
    21:15 phishing email."""
    replies: dict[str, dict[str, Any]] = {}
    for rec in recordings:
        asks = {
            e["payload"].get("request_id"): e["payload"].get("fields")
            for e in rec.events
            if e["kind"] == "a2a.request"
        }
        for e in rec.events:
            if e["kind"] != "a2a.reply":
                continue
            p = e["payload"]
            rtype = str(p.get("request_type"))
            example = {
                "request_type": rtype,
                "request": asks.get(p.get("request_id")) or {},
                "reply": p.get("reply_in"),
                "recording": rec.name,
            }
            known = replies.get(rtype)
            if known is None or (_planted(p.get("reply_in")) and not _planted(known["reply"])):
                replies[rtype] = example
    lock = None
    for rec in recordings:
        if rec.run is None:
            continue
        held = [h for h in rec.run.runtime.store.holds(state=None, limit=200) if h.kind == "tool"]
        if held:
            h = sorted(held, key=lambda x: x.created)[-1]
            lock = {
                "hold": {"agent": h.agent, "tool": h.tool, "args": h.args, "hold_id": h.hold_id},
                "fingerprint": h.fingerprint,
                "recording": rec.name,
            }
            break
    mail = next(
        (m for m in load_raw("work_mail")["messages"] if m.get("attack") == "phishing"), None
    )
    return {
        "reply": [replies[t] for t in ("availability", "travel_limits", "needs") if t in replies],
        "ask": [
            {"request_type": "reasons", "fields": {"question": TRICKED_REASONS}},
            {
                "request_type": "availability",
                "fields": {
                    "start_date": "2026-10-12",
                    "end_date": "2026-10-15",
                    "include": "titles and places, please",
                },
            },
            {"request_type": "availability", "fields": TRIP},
        ],
        "mail": {k: mail[k] for k in ("from", "subject", "body", "body_html")} if mail else None,
        "lock": lock,
    }


# Rate limit --------------------------------------------------------------------------------------


class Buckets:
    """A token bucket per client address: RATE_BURST at once, refilled at RATE_PER_MINUTE."""

    def __init__(
        self,
        per_minute: float = RATE_PER_MINUTE,
        burst: float = RATE_BURST,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = per_minute / 60.0
        self.burst = burst
        self.now = now
        self._buckets: dict[str, tuple[float, float]] = {}

    def take(self, key: str) -> bool:
        now = self.now()
        tokens, last = self._buckets.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if len(self._buckets) > 10_000:
            # Forget clients that have been away long enough to be full again.
            full_after = self.burst / self.rate
            self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < full_after}
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1, now)
        return True


def client_address(request: Request) -> str:
    """The visitor's address. Behind Caddy (a loopback peer, or Docker's bridge when the demo
    runs in a container) it is the last X-Forwarded-For entry, which Caddy sets; from anywhere
    else the header is ignored."""
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    try:
        address = ipaddress.ip_address(peer)
        trusted = any(address in network for network in PROXY_NETWORKS)
    except ValueError:
        trusted = False
    if forwarded and trusted:
        last = forwarded.split(",")[-1].strip()
        return last or peer
    return peer


async def capped_json(request: Request) -> Any:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY:
        raise HTTPException(413, "request body over 16 KB")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "request body over 16 KB")
    try:
        return json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(400, "the body must be JSON") from exc


# The app -----------------------------------------------------------------------------------------


class DemoServer:
    """The ASGI entry point: /r/NAME/... goes to that recording's replayed Steward (GET and
    HEAD only), everything else to the demo app (console, info, replay, playground)."""

    def __init__(self, outer: FastAPI, recordings: dict[str, Recording]) -> None:
        self.outer = outer
        self.recordings = recordings

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[Any]],
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/r/"):
            name, _, rest = scope["path"][3:].partition("/")
            rec = self.recordings.get(name)
            if rec is None or rec.run is None:
                await JSONResponse({"detail": "no such recording"}, 404)(scope, receive, send)
                return
            if scope["method"] not in {"GET", "HEAD"}:
                await JSONResponse({"detail": "the public demo is read-only"}, 403)(
                    scope, receive, send
                )
                return
            child = dict(scope)
            child["path"] = "/" + rest
            child["raw_path"] = child["path"].encode()
            await rec.run.app(child, receive, send)
            return
        await self.outer(scope, receive, send)


def create_demo_app(
    folder: Path = DEMO_DIR, console_dir: Path | None = None, buckets: Buckets | None = None
) -> DemoServer:
    recordings = {rec.name: rec for rec in load_manifest(folder)}
    order = list(recordings)
    buckets = buckets or Buckets()
    replay_lock = asyncio.Lock()
    state: dict[str, Any] = {"examples": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        async with AsyncExitStack() as stack:
            for rec in recordings.values():
                try:
                    rec.run = await stack.enter_async_context(replayed(rec.events, mode="demo"))
                except Exception:
                    log.exception("could not replay recording %s", rec.name)
                    continue
                rec.summary = summarise(rec)
                result = rec.run.result
                log.info(
                    "recording %s: replay %s, %s of %s decisions",
                    rec.name,
                    "PASS" if result["ok"] else "FAIL",
                    result["compared"],
                    result["recorded_decisions"],
                )
            state["examples"] = examples(list(recordings.values()))
            yield

    app = FastAPI(
        title="Steward demo",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def ready() -> list[Recording]:
        return [recordings[n] for n in order if recordings[n].run is not None]

    def limited(request: Request) -> None:
        if not buckets.take(client_address(request)):
            raise HTTPException(429, "too many requests; try again in a few seconds")

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse("/console/")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "mode": "demo", "recordings": len(ready())}

    @app.get("/demo/info")
    async def info() -> dict[str, Any]:
        live = ready()
        return {
            "mode": "demo",
            "default": live[0].name if live else None,
            "recordings": [
                {
                    **rec.summary,
                    "rerun": rec.rerun,
                }
                for rec in live
            ],
            "examples": state["examples"],
            "limits": {
                "body_bytes": MAX_BODY,
                "posts_per_minute": RATE_PER_MINUTE,
                "replay_cache_s": REPLAY_CACHE_S,
            },
        }

    @app.post("/demo/replay")
    async def rerun(request: Request) -> dict[str, Any]:
        limited(request)
        body = await capped_json(request)
        name = str((body or {}).get("recording") or "")
        rec = recordings.get(name)
        if rec is None or rec.run is None:
            raise HTTPException(404, "no such recording")

        def cached() -> dict[str, Any] | None:
            if rec.rerun is not None and time.monotonic() - rec.rerun_at < REPLAY_CACHE_S:
                return {**rec.rerun, "cached": True}
            return None

        hit = cached()
        if hit is not None:
            return hit
        async with replay_lock:
            hit = cached()
            if hit is not None:
                return hit
            result = await replay_events(rec.events)
            result["ran_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            rec.rerun, rec.rerun_at = result, time.monotonic()
        return {**result, "cached": False}

    @app.post("/demo/check")
    async def playground(request: Request) -> dict[str, Any]:
        limited(request)
        raw = await capped_json(request)
        try:
            body = CheckIn.model_validate(raw)
        except ValidationError as exc:
            raise HTTPException(422, "the check request does not fit the playground") from exc
        live = ready()
        if not live:
            raise HTTPException(503, "no recording is loaded")
        rec = recordings.get(body.recording or "") or live[0]
        if rec.run is None:
            raise HTTPException(404, "no such recording")
        return await check(body, rec.run)

    if console_dir is not None and console_dir.is_dir():
        app.mount("/console", StaticFiles(directory=console_dir, html=True), name="console")
    return DemoServer(app, recordings)
