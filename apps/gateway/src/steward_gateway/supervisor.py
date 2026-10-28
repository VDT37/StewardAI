"""Steward's supervisor (Phase 3): the watcher, the scheduler and the morning brief.

The watcher reads every new flight-log event and pauses an agent that crosses a hard limit
(steward_core.watcher). It runs inside the event's own append, so the pause is in place before
the agent's next call, and a replay of the same events pauses at the same event.

The scheduler runs jobs at a London time on Steward's clock, so in a story run the brief comes
at 07:30 story time. A job runs when the clock passes its time, once per due time and counter
scope (job_runs), and each run is logged with a system_action field so replay runs it again at
the same moment. After a restart a live job that was due while Steward was down runs once.

The brief job picks the facts in code (steward_core.brief), asks the helper model for the
summary lines, checks them, and falls back to the template. The one question is code-written
and is answered with the same fingerprints as the console's cards.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from steward_core.brief import (
    SYSTEM_PROMPT,
    AgentNight,
    Booking,
    BriefFacts,
    Mail,
    Notice,
    Pause,
    Question,
    QuestionHold,
    Refused,
    StaleWrite,
    check_text,
    question_line,
    summary_lines,
)
from steward_core.clock import (
    LONDON,
    iso_ms,
    london_day,
    london_day_end,
    london_day_start,
    parse_iso,
)
from steward_core.metering import call_cost
from steward_core.store import Event, Hold
from steward_core.watcher import HardLimits, baseline, observed
from steward_fixtures import home_address_parts, planted_details

if TYPE_CHECKING:
    from steward_gateway.runtime import Runtime

HELPER_AGENT = "steward"
CALL_KINDS = frozenset({"tool.call", "tool.held", "tool.refused", "tool.denied", "hold.status"})
ASK_KINDS = frozenset({"a2a.request", "a2a.refused", "a2a.held"})


# Watcher ----------------------------------------------------------------------------------------


class Watcher:
    def __init__(self, runtime: Runtime) -> None:
        settings = runtime.settings.policy.watcher
        assert settings is not None
        self.rt = runtime
        self.limits = HardLimits(settings)

    def on_event(self, event: Event) -> None:
        """Store listener: count the event; pause its agent at a hard limit. Never raises (a
        listener that raises is dropped by the store)."""
        try:
            seen = observed(event)
            if seen is None or seen.agent not in self.rt.settings.policy.agents:
                return
            breach = self.limits.observe(seen)
            if breach is not None and seen.agent not in self.rt.passes.suspended:
                self.rt.watcher_pause(seen.agent, breach.as_dict(), seen.at)
        except Exception as exc:  # pragma: no cover - defensive
            with contextlib.suppress(Exception):
                self.rt.recorder.record("system", "watcher.error", {"error": repr(exc)[:300]})


# Scheduler --------------------------------------------------------------------------------------

JobRun = Callable[[datetime], Awaitable[None]]


@dataclass
class Job:
    name: str
    at: str  # HH:MM, Europe/London
    run: JobRun
    # Live sessions only (the backup): never in a story run or a replay.
    live_only: bool = False


def due_before(at: str, now: datetime) -> datetime:
    """The most recent HH:MM Europe/London at or before now."""
    hours, minutes = (int(x) for x in at.split(":"))
    local = now.astimezone(LONDON)
    due = datetime.combine(local.date(), dtime(hours, minutes), tzinfo=LONDON)
    if due > local:
        due = datetime.combine(
            local.date() - timedelta(days=1), dtime(hours, minutes), tzinfo=LONDON
        )
    return due


class Scheduler:
    TICK_S = 1.0

    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime
        self.jobs: dict[str, Job] = {}
        # Per counter scope: Steward's clock at the last tick, to see a due time being passed.
        self._last_seen: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    def add(self, job: Job) -> None:
        self.jobs[job.name] = job

    def observe(self) -> None:
        """Note the clock now, so only due times after this moment run in this scope (a story
        run calls it when it sets the clock)."""
        self._last_seen[self.rt.recorder.scope] = self.rt.clock.now()

    async def tick(self) -> list[str]:
        """Run every job whose due time the clock passed since the last tick."""
        async with self._lock:
            now = self.rt.clock.now()
            scope = self.rt.recorder.scope
            previous = self._last_seen.get(scope)
            self._last_seen[scope] = now
            ran: list[str] = []
            for job in self.jobs.values():
                if job.live_only and scope != "live":
                    continue
                due = due_before(job.at, now)
                if previous is None or now < previous:
                    # First look at this scope, or the clock went back (a story run set it):
                    # catch up a live job missed while Steward was down, nothing else.
                    last = self.rt.store.last_job_run(job.name, scope)
                    if scope != "live" or last is None or iso_ms(due) <= last:
                        continue
                elif not previous < due <= now:
                    continue
                if await self.run_job(job.name, due):
                    ran.append(job.name)
            return ran

    async def run_job(self, name: str, due: datetime) -> bool:
        """Run one job for one due time, once (also called by replay with the recorded due)."""
        job = self.jobs.get(name)
        if job is None:
            return False
        scope = self.rt.recorder.scope
        if not self.rt.store.claim_job_run(name, scope, iso_ms(due)):
            return False
        if not job.live_only:
            self.rt.recorder.record(
                "system",
                "scheduler.run",
                {
                    "job": name,
                    "due": iso_ms(due),
                    "system_action": {"job": name, "due": iso_ms(due)},
                },
                at=due,
            )
        try:
            await job.run(due)
        except Exception as exc:
            self.rt.recorder.record(
                "system", "scheduler.failed", {"job": name, "error": repr(exc)[:300]}
            )
        return True

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self.tick()
            await asyncio.sleep(self.TICK_S)


# Backup -----------------------------------------------------------------------------------------


def backup_job(runtime: Runtime) -> JobRun:
    async def run(due: datetime) -> None:
        settings = runtime.settings.policy.backup
        assert settings is not None
        folder = Path(settings.directory)
        folder.mkdir(parents=True, exist_ok=True)
        name = f"steward-{london_day(due).isoformat()}.db"
        await asyncio.to_thread(runtime.store.backup_to, folder / name)
        copies = sorted(folder.glob("steward-*.db"))
        for old in copies[: max(0, len(copies) - settings.keep)]:
            old.unlink(missing_ok=True)
        runtime.recorder.record("system", "backup.done", {"file": name, "kept": settings.keep})

    return run


# Morning brief ----------------------------------------------------------------------------------


def _hm(ts: str) -> str:
    return parse_iso(ts).astimezone(LONDON).strftime("%H:%M")


def _day(value: str | datetime) -> str:
    moment = parse_iso(value) if isinstance(value, str) else value
    return moment.astimezone(LONDON).strftime("%a %d %b").replace(" 0", " ")


def _date_label(iso_date: str) -> str:
    from datetime import date

    return date.fromisoformat(iso_date).strftime("%a %d %b").replace(" 0", " ")


class Briefing:
    """Builds the brief's facts, has them written, checks the text and logs the result."""

    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime

    # Facts ------------------------------------------------------------------------------------

    def window(self, now: datetime) -> tuple[str, list[Event]]:
        """(since, the events since the last brief) in this session (a story run) or, live,
        over the last day."""
        rt = self.rt
        session = rt.recorder.session_id
        if rt.recorder.scope == "live":
            since_ts = iso_ms(now - timedelta(days=1))
            events = [
                e
                for e in rt.store.events(limit=5000, newest_first=False, since_ts=since_ts)
                if e.ts <= iso_ms(now)
            ]
        else:
            events = [
                e
                for e in rt.store.events(limit=5000, newest_first=False, session_id=session)
                if e.ts <= iso_ms(now)
            ]
            since_ts = events[0].ts if events else iso_ms(now)
        last = [e for e in events if e.kind == "brief.facts"]
        if last:
            since_ts = last[-1].ts
            events = [e for e in events if e.seq > last[-1].seq]
        return since_ts, events

    def facts(self, brief_id: str, now: datetime) -> BriefFacts:
        rt = self.rt
        policy = rt.settings.policy
        since_ts, events = self.window(now)
        session = rt.recorder.session_id if rt.recorder.scope != "live" else None

        agents = []
        for name, rules in policy.agents.items():
            mine = [e for e in events if e.agent == name]
            spend = sum(
                (
                    Decimal(str(e.payload.get("cost_usd") or "0"))
                    for e in mine
                    if e.kind == "model.call"
                ),
                Decimal(0),
            )
            day = london_day(now)
            today = rt.store.spend_since(
                name, london_day_start(day), until_ts=london_day_end(day), session_id=session
            )
            budget = rules.budget_usd_per_day
            percent = int(today * 100 / budget) if budget > 0 else 0
            agents.append(
                AgentNight(
                    agent=name,
                    model=rules.model,
                    model_calls=sum(1 for e in mine if e.kind == "model.call"),
                    tool_calls=sum(1 for e in mine if e.kind in CALL_KINDS),
                    asks=sum(1 for e in mine if e.kind in ASK_KINDS),
                    answered=sum(1 for e in mine if e.kind == "a2a.reply"),
                    refused=sum(1 for e in mine if e.kind in {"tool.refused", "a2a.refused"}),
                    held=sum(1 for e in mine if e.kind in {"tool.held", "a2a.held"}),
                    spend_usd=f"{spend:.4f}",
                    budget_usd=f"{budget:.2f}",
                    spend_percent=percent,
                )
            )

        stand_in_bookings = {
            str(b)
            for s in rt.store.stand_ins(rt.recorder.session_id)
            for b in s.get("booking_ids", [])
        }
        bookings = []
        for booking in rt.world.bookings.values():
            bookings.append(
                Booking(
                    kind=str(booking.get("kind")),
                    date=_date_label(str(booking.get("date"))),
                    time=str(booking.get("depart") or booking.get("pickup_time") or ""),
                    price_gbp=str(booking.get("price_gbp")),
                    changed=int(booking.get("changes") or 0) > 0,
                    stand_in=str(booking.get("booking_id")) in stand_in_bookings,
                )
            )

        refused = []
        for e in events:
            if e.kind == "a2a.refused":
                refused.append(
                    Refused(
                        when=_hm(e.ts), agent=str(e.agent), what=str(e.payload.get("request_type"))
                    )
                )
            elif e.kind == "tool.refused":
                call = e.payload.get("call") or {}
                refused.append(
                    Refused(when=_hm(e.ts), agent=str(e.agent), what=str(call.get("tool")))
                )
        asks_held = sum(
            1
            for e in events
            if e.kind == "a2a.held" and e.payload.get("rule") in {"daily_limit", "burst"}
        )
        notices = [
            Notice(
                when=_hm(e.ts),
                date=_date_label(str(n.get("date"))),
                start=str(n.get("start")),
                end=str(n.get("end")),
                can_move=bool(n.get("can_move")),
            )
            for e in events
            if e.kind == "a2a.notice"
            for n in e.payload.get("notices") or []
        ]
        stale = []
        for e in events:
            if e.kind == "tool.held" and e.payload.get("rule") == "stale_write":
                hold = rt.holds.get(str(e.payload.get("hold_id")), now)
                current = (e.payload.get("details") or {}).get("current") or {}
                stale.append(
                    StaleWrite(
                        when=_hm(e.ts),
                        item=str(current.get("title") or "an item"),
                        state=hold.state if hold else "unknown",
                    )
                )
        verdicts = {
            c["target"]: str(c["data"].get("verdict")) for c in rt.store.controls("mail_verdict")
        }
        mail: dict[str, Mail] = {}
        for e in events:
            if e.kind != "mail.screened" or e.payload.get("message_id") in mail:
                continue
            message_id = str(e.payload.get("message_id"))
            received = e.payload.get("received")
            mail[message_id] = Mail(
                when=parse_iso(str(received)).astimezone(LONDON).strftime("%H:%M")
                if received
                else _hm(e.ts),
                agent=str(e.agent),
                sender_domain=str(e.payload.get("sender_domain")),
                subject=str(e.payload.get("subject"))[:120],
                signs=len(e.payload.get("signs") or []),
                links_withheld=0
                if e.payload.get("released_by_owner")
                else len(e.payload.get("links") or []),
                reported_by_agent=message_id in rt.world.reported,
                owner_verdict=verdicts.get(message_id),
            )
        pauses = [
            Pause(
                when=_hm(e.ts),
                agent=str(e.agent),
                rule=str(e.payload.get("rule")),
                detail=str(e.payload.get("detail")),
                still_paused=str(e.agent) in rt.passes.suspended,
            )
            for e in events
            if e.kind == "watcher.paused"
        ]
        pending = [h for h in rt.holds.pending(now) if session is None or h.session_id == session]
        return BriefFacts(
            brief_id=brief_id,
            date=_day(now),
            since=f"{_day(since_ts)} {_hm(since_ts)}",
            until=f"{_day(now)} {now.astimezone(LONDON).strftime('%H:%M')}",
            agents=tuple(agents),
            bookings=tuple(bookings),
            refused=tuple(refused),
            asks_held=asks_held,
            notices=tuple(notices),
            stale_writes=tuple(stale),
            mail=tuple(mail.values()),
            pauses=tuple(pauses),
            pending=len(pending),
            question=self.question(pending, notices),
            chain_ok=rt.store.verify().ok,
        )

    def question(self, pending: list[Hold], notices: list[Notice]) -> Question | None:
        """The one question: paid changes first (one question per trip), then a stale write,
        then the oldest other call that waits."""
        rt = self.rt
        policy = rt.settings.policy

        def qhold(hold: Hold) -> QuestionHold:
            return QuestionHold(
                hold_id=hold.hold_id,
                fingerprint=hold.fingerprint,
                tool=hold.tool,
                choices=tuple((c["id"], c["label"]) for c in hold.choices),
            )

        ordered = sorted(pending, key=lambda h: (h.created, h.hold_id))
        paid = [
            h
            for h in ordered
            if h.kind == "tool"
            and h.tool_id
            and policy.tools.get(h.tool_id)
            and policy.tools[h.tool_id].tool_class == "change_paid"
        ]
        if paid:
            trip = str((paid[0].details.get("quote") or {}).get("trip_date") or "")
            group = [h for h in paid if (h.details.get("quote") or {}).get("trip_date") == trip]
            changes = [self._change_text(h) for h in group]
            label = _date_label(trip) if trip else "the trip"
            reason = ""
            clash = [n for n in notices if n.date == label]
            if clash:
                n = clash[0]
                reason = (
                    f"Your new commitment on {n.date} ({n.start} to {n.end}, now busy) "
                    "affects the trip. "
                )
            text = f"{reason}Change the {label} bookings: {_and(changes)}?"
            return Question(
                kind="change_paid",
                text=text,
                holds=tuple(qhold(h) for h in group),
                approve="approve",
                deny="deny",
            )
        stale = [h for h in ordered if h.choices and h.choices[0].get("label") == "Use this write"]
        if stale:
            h = stale[0]
            current = h.details.get("current") or {}
            proposed = (h.details.get("proposed") or {}).get("fields") or {}
            title = str(current.get("title") or "an item")
            change = ", ".join(f"{k} to '{v}'" for k, v in proposed.items()) or "a change"
            text = (
                f"The {h.agent} agent wrote to the to-do item '{title}' after it had changed "
                f"(it would set {change}). Use the {h.agent} agent's write?"
            )
            return Question(
                kind="stale_write", text=text, holds=(qhold(h),), approve="approve", deny="deny"
            )
        if ordered:
            h = ordered[0]
            approve = next((c["id"] for c in h.choices if c["id"] != "deny"), "approve")
            label = next((c["label"] for c in h.choices if c["id"] == approve), "Allow")
            text = f"The {h.agent} agent's {h.tool} is waiting for you ({h.reason}). {label}?"
            return Question(
                kind="other", text=text, holds=(qhold(h),), approve=approve, deny="deny"
            )
        return None

    def _change_text(self, hold: Hold) -> str:
        world = self.rt.world
        booking = world.bookings.get(str(hold.args.get("booking_id")), {})
        if booking.get("kind") == "train" and hold.args.get("option_id"):
            try:
                option = world.train(str(hold.args["option_id"]))
            except Exception:
                return f"change booking {hold.args.get('booking_id')}"
            return (
                f"move the train from {booking.get('depart')} to {option['depart']} "
                f"(arrives {option['arrive']}, £{option['price_gbp']})"
            )
        if booking.get("kind") == "taxi" and hold.args.get("pickup_time"):
            return (
                f"move the taxi pickup from {booking.get('pickup_time')} to "
                f"{hold.args['pickup_time']}"
            )
        return f"change booking {hold.args.get('booking_id')}"

    # Writing ----------------------------------------------------------------------------------

    def forbidden(self) -> list[str]:
        return [d["text"] for d in planted_details()] + home_address_parts()

    async def write(self, facts: BriefFacts) -> tuple[str, dict[str, Any]]:
        """(the summary lines, info): from the helper model when its text passes the check,
        else from the template."""
        rt = self.rt
        helper = rt.settings.policy.helper
        brief_settings = rt.settings.policy.brief
        max_chars = brief_settings.max_chars if brief_settings else 900
        recorded = await rt.tool_door.gate.wait("brief.written", facts.brief_id)
        if recorded is not None:
            return str(recorded.get("summary") or ""), {
                k: recorded.get(k) for k in ("source", "model", "problems")
            }
        template = "\n".join(summary_lines(facts))
        if helper is None:
            return template, {"source": "template", "model": None, "problems": ["no helper"]}
        text, error = await self._ask_helper(facts)
        if error is not None or text is None:
            return template, {"source": "template", "model": helper.model, "problems": [error]}
        problems = check_text(text, facts, forbidden=self.forbidden(), max_chars=max_chars)
        if problems:
            return template, {"source": "template", "model": helper.model, "problems": problems[:8]}
        return text.strip(), {"source": "model", "model": helper.model, "problems": []}

    async def _ask_helper(self, facts: BriefFacts) -> tuple[str | None, str | None]:
        rt = self.rt
        helper = rt.settings.policy.helper
        assert helper is not None
        client = rt.model_door.client
        if client is None:
            return None, "no model client"
        now = rt.clock.now()
        day = london_day(now)
        spent = rt.store.spend_since(
            HELPER_AGENT, london_day_start(day), until_ts=london_day_end(day), kind="helper.call"
        )
        if spent >= helper.budget_usd_per_day:
            return None, "the helper's daily budget is used up"
        body: dict[str, Any] = {
            "model": helper.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(facts.for_model(), ensure_ascii=False)},
            ],
            "max_tokens": helper.max_tokens,
            "temperature": 0.2,
        }
        if helper.reasoning_effort:
            body["reasoning_effort"] = helper.reasoning_effort
        headers = {
            "Authorization": f"Bearer {rt.settings.nebius_api_key}",
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        status, usage, text, error = 0, None, None, None
        try:
            response = await asyncio.wait_for(
                client.post(
                    f"{rt.settings.upstream_url}/chat/completions", json=body, headers=headers
                ),
                helper.timeout_s,
            )
            status = response.status_code
            data = response.json() if status == 200 else {}
            usage = data.get("usage") if isinstance(data, dict) else None
            choice = (data.get("choices") or [{}])[0] if isinstance(data, dict) else {}
            content = (choice.get("message") or {}).get("content")
            text = content if isinstance(content, str) else None
            if status != 200:
                error = f"HTTP {status}"
            elif not text:
                error = "the model returned no text"
        except Exception as exc:
            error = type(exc).__name__ or "error"
        cost = call_cost(usage, rt.settings.policy.prices.get(helper.model))
        rt.recorder.record(
            "model",
            "helper.call",
            {
                "model": helper.model,
                "purpose": "brief",
                "status": status,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "usage": usage if isinstance(usage, dict) else None,
                "cost_usd": cost,
                "error": error,
            },
            agent=HELPER_AGENT,
        )
        return text, error

    async def run(self, due: datetime) -> BriefFacts:
        """The brief for the night up to `due`. Everything is computed at `due`, not at the
        moment the job happens to run, so a replay computes exactly the same facts."""
        rt = self.rt
        brief_id = rt.recorder.ids.next("b")
        facts = self.facts(brief_id, due)
        rt.recorder.record(
            "system",
            "brief.facts",
            {"brief_id": brief_id, "due": iso_ms(due), "facts": facts.model_dump(mode="json")},
            at=due,
        )
        rt.recorder.record(
            "system",
            "brief.baselines",
            {"brief_id": brief_id, "baselines": self.baselines(facts)},
            at=due,
        )
        summary, info = await self.write(facts)
        text = summary + "\n" + question_line(facts)
        rt.recorder.record(
            "system",
            "brief.written",
            {"brief_id": brief_id, "summary": summary, "text": text, **info},
            at=max(due, rt.clock.now()),
        )
        telegram = rt.extras.get("telegram")
        if telegram is not None:
            with contextlib.suppress(Exception):
                await telegram.send_brief(text, facts)
        return facts

    def baselines(self, facts: BriefFacts) -> list[dict[str, Any]]:
        """Tonight against earlier briefs' nights (this store's own history), report only."""
        history: dict[tuple[str, str], list[float]] = {}
        for e in self.rt.store.events(kind="brief.facts", limit=200, newest_first=False):
            if e.payload.get("brief_id") == facts.brief_id:
                continue
            for night in (e.payload.get("facts") or {}).get("agents", []):
                for metric in ("model_calls", "tool_calls", "asks"):
                    history.setdefault((night["agent"], metric), []).append(float(night[metric]))
        out = []
        for night in facts.agents:
            for metric in ("model_calls", "tool_calls", "asks"):
                value = float(getattr(night, metric))
                result = baseline(metric, value, history.get((night.agent, metric), [])[-14:])
                out.append({"agent": night.agent, **result.as_dict()})
        return out


def _and(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def brief_html(text: str) -> str:
    lines = [html.escape(line) for line in text.splitlines() if line.strip()]
    return "<b>Morning brief</b>\n" + "\n".join(lines)


def latest_brief(runtime: Runtime) -> dict[str, Any] | None:
    """The newest brief with its facts and the question's holds as they stand now."""
    written = runtime.store.events(kind="brief.written", limit=1)
    if not written:
        return None
    w = written[0]
    brief_id = w.payload.get("brief_id")
    facts_event = next(
        (
            e
            for e in runtime.store.events(kind="brief.facts", limit=20)
            if e.payload.get("brief_id") == brief_id
        ),
        None,
    )
    facts = (facts_event.payload.get("facts") if facts_event else None) or {}
    question = facts.get("question")
    holds = []
    if question:
        for q in question.get("holds", []):
            hold = runtime.holds.get(str(q["hold_id"]))
            holds.append({**q, "state": hold.state if hold else "unknown"})
    return {
        "brief_id": brief_id,
        "ts": w.ts,
        "session_id": w.session_id,
        "text": w.payload.get("text"),
        "summary": w.payload.get("summary"),
        "source": w.payload.get("source"),
        "model": w.payload.get("model"),
        "problems": w.payload.get("problems"),
        "facts": facts,
        "question": {**question, "holds": holds} if question else None,
    }
