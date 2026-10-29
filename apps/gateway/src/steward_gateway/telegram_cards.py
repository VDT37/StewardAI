"""Telegram approval cards (Phase 2 Part I, a Should item, off unless configured).

With TELEGRAM_BOT_TOKEN and STEWARD_TELEGRAM_OWNER_ID set, every new hold is sent to the owner's
chat as a card: what is held, why, the choices and the fingerprint. Each button carries a short
random token (Telegram's callback_data is 1 to 64 bytes, a full fingerprint alone is 64 hex
characters), mapped here to the hold and the choice for holds.link_minutes. A press is accepted
only from the owner's user id; the decision itself goes through the hold book with the stored
fingerprint, like a console approval.

Polling (getUpdates) needs only outbound HTTPS, so it works while the VM allows SSH only. Only
one poller per bot token may run, so never start a second Steward with the same token.

Phase 3: the morning brief goes to the same chat, with Approve and Deny buttons for its one
question. A question can cover several holds (a train and a taxi for one trip): its buttons
decide each of them with its own stored fingerprint. Calls held in the quiet hours send no card
while the owner sleeps; right after the brief they arrive as cards (calls of one agent held by
the same rule share one card), so everything held overnight can be decided from the phone.
"""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from steward_core.brief import BriefFacts
from steward_core.clock import LONDON, parse_iso
from steward_core.store import Hold
from steward_gateway.holds import HoldError, short_fingerprint
from steward_gateway.prompted import prompted_by

if TYPE_CHECKING:
    from steward_gateway.runtime import Runtime
    from steward_gateway.settings import Settings

SEND_GAP_S = 1.0

# The Bot API puts the bot token in every request URL, and httpx logs each request's URL at
# INFO, so with Telegram on the token reached the journal on every poll. httpx stays at WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)


@dataclass
class ButtonToken:
    hold_id: str
    choice: str
    expires: float


@dataclass
class QuestionToken:
    # (hold id, choice, fingerprint) for every hold the brief's question covers.
    decisions: list[tuple[str, str, str]]
    expires: float


@dataclass
class BriefMessage:
    """A brief sent with a question: edited to say it is answered once its holds are decided."""

    text: str
    hold_ids: tuple[str, ...]


def in_quiet_hours(moment: datetime, quiet_from: str | None, until: str) -> bool:
    """Whether a moment falls in the quiet hours [quiet_from, until), London time, which may run
    across midnight (22:00 to 07:30)."""
    if quiet_from is None or quiet_from == until:
        return False
    now = moment.astimezone(LONDON).strftime("%H:%M")
    if quiet_from < until:
        return quiet_from <= now < until
    return now >= quiet_from or now < until


AGENTS = {"work": "Work agent", "personal": "Personal agent"}
OUTCOMES = {
    "approved": "\u2705 Approved",
    "done": "\u2705 Approved, and it ran once",
    "failed": "\u26a0\ufe0f Approved, but the call failed",
    "unknown": "\u26a0\ufe0f Approved, outcome unknown",
    "denied": "\u274c Denied",
    "expired": "\u231b Expired unanswered",
}


def _clip(text: str, limit: int) -> str:
    """Shorten before escaping, so an HTML entity is never cut in half."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def _call_lines(hold: Hold) -> list[str]:
    args = hold.args.get("arguments", hold.args) if isinstance(hold.args, dict) else hold.args
    lines = [f"<code>{html.escape(hold.tool)}</code>"]
    if isinstance(args, dict):
        flat: dict[str, Any] = {}
        for key, value in args.items():
            if isinstance(value, dict):
                flat.update(value)
            else:
                flat[key] = value
        for key, value in list(flat.items())[:8]:
            lines.append(f"\u2022 {html.escape(str(key))}: {html.escape(_clip(str(value), 90))}")
    elif args:
        lines.append(html.escape(_clip(str(args), 200)))
    return lines


def card_html(hold: Hold, prompted: str | None = None) -> str:
    """The card: what is held, the rule, what started the agent's run, the call and the
    fingerprint (HTML parse mode), one short block each. The choices are the buttons."""
    what = hold.details.get("card") or f"The {hold.agent} agent wants to call {hold.tool}."
    who = AGENTS.get(hold.agent, hold.agent)
    blocks = [
        f"\u23f8\ufe0f <b>Held for you</b> \u00b7 {html.escape(who)}\n"
        f"{html.escape(_clip(str(what), 300))}",
        f"<b>Why</b>\n{html.escape(_clip(hold.reason, 300))}",
    ]
    if prompted:
        blocks.append(f"<b>Prompted by</b>\n{html.escape(_clip(prompted, 220))}")
    blocks.append("<b>Call</b>\n" + "\n".join(_call_lines(hold)))
    blocks.append(
        f"\U0001f512 Locked to <code>{short_fingerprint(hold.fingerprint)}</code>. "
        "Any change voids the approval."
    )
    return "\n\n".join(blocks)


def outcome_line(states: list[str]) -> str:
    shown = [OUTCOMES.get(s, s) for s in sorted(set(states))]
    return "\n\n<b>" + html.escape("; ".join(shown)) + "</b>"


def group_html(holds: list[Hold], prompted: str | None = None) -> str:
    """One card for several calls of one agent held by the same rule while the owner slept."""
    first = holds[0]
    who = AGENTS.get(first.agent, first.agent)
    items = []
    for hold in holds[:12]:
        what = hold.details.get("card") or _summary(hold)
        items.append(f"\u2022 {html.escape(_clip(str(what), 120))}")
    blocks = [
        f"\U0001f319 <b>Held while you slept</b> \u00b7 {html.escape(who)}\n"
        f"{len(holds)} calls held by the same rule",
        f"<b>Why</b>\n{html.escape(_clip(first.reason, 300))}",
    ]
    if prompted:
        blocks.append(f"<b>Prompted by</b>\n{html.escape(_clip(prompted, 220))}")
    blocks.append("<b>Calls</b>\n" + "\n".join(items))
    blocks.append("\U0001f512 Each call is locked to its own fingerprint.")
    return "\n\n".join(blocks)


def _summary(hold: Hold) -> str:
    args = hold.args.get("arguments", hold.args) if isinstance(hold.args, dict) else hold.args
    if hold.tool == "ask" and isinstance(args, dict):
        fields = args.get("fields") or {}
        days = " to ".join(dict.fromkeys(str(v) for v in fields.values())) if fields else ""
        return f"ask {args.get('request_type')} {days}".strip()
    return f"{hold.tool} {args}"


def brief_html(text: str) -> str:
    """The brief, one topic per block: "Topic: words" becomes a bold topic over its words."""
    blocks = ["\u2600\ufe0f <b>Morning brief</b>"]
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        topic, sep, rest = line.partition(": ")
        if sep and len(topic) <= 30:
            icon = "\u2753 " if topic.lower().startswith("one question") else ""
            blocks.append(f"{icon}<b>{html.escape(topic)}</b>\n{html.escape(rest)}")
        else:
            blocks.append(html.escape(line))
    return "\n\n".join(blocks)


class TelegramCards:
    def __init__(self, token: str, owner_id: int, runtime: Runtime) -> None:
        self.token = token
        self.owner_id = owner_id
        self.runtime = runtime
        self.buttons: dict[str, ButtonToken] = {}
        self.questions: dict[str, QuestionToken] = {}
        self.messages: dict[str, int] = {}
        # Hold id -> the London time its card was sent (for the brief to point at it).
        self.card_times: dict[str, str] = {}
        self.briefs: dict[int, BriefMessage] = {}
        self._app: Any = None
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    def from_settings(cls, settings: Settings, runtime: Runtime) -> TelegramCards | None:
        if not settings.telegram_token or settings.telegram_owner_id is None:
            return None
        return cls(settings.telegram_token, settings.telegram_owner_id, runtime)

    # Lifecycle (in the FastAPI lifespan; never run_polling, which blocks and owns its loop) ----

    async def start(self) -> None:
        from telegram.ext import Application, CallbackQueryHandler

        self._app = Application.builder().token(self.token).build()
        self._app.add_handler(CallbackQueryHandler(self._on_query))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(allowed_updates=["callback_query"])

    async def stop(self) -> None:
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    # Holds -> cards ---------------------------------------------------------------------------

    def quiet(self, hold: Hold) -> bool:
        """A call held in the quiet hours waits for the morning brief instead of a card."""
        brief = self.runtime.settings.policy.brief
        if brief is None:
            return False
        return in_quiet_hours(parse_iso(hold.created), brief.quiet_from, brief.at)

    def on_hold(self, hold: Hold, event: str) -> None:
        if self._app is None:
            return
        if event == "created":
            if self.quiet(hold):
                return
            coro = self.send_card(hold)
        else:
            coro = self.close(hold)
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self, hold: Hold) -> None:
        await self.close_card(hold)
        await self.close_briefs(hold)

    def button_token(self, hold: Hold, choice: str, ttl_s: float | None = None) -> str:
        token = secrets.token_urlsafe(9)
        ttl = ttl_s if ttl_s is not None else self.runtime.settings.policy.holds.link_minutes * 60
        self.buttons[token] = ButtonToken(hold.hold_id, choice, time.monotonic() + ttl)
        return token

    async def send_card(self, hold: Hold, ttl_s: float | None = None) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        c["label"], callback_data=f"d:{self.button_token(hold, c['id'], ttl_s)}"
                    )
                    for c in hold.choices
                ]
            ]
        )
        async with self._send_lock:
            message = await self._app.bot.send_message(
                chat_id=self.owner_id,
                text=card_html(hold, prompted_by(self.runtime, hold)),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            self.messages[hold.hold_id] = message.message_id
            self.card_times[hold.hold_id] = (
                parse_iso(hold.created).astimezone(LONDON).strftime("%H:%M")
            )
            await asyncio.sleep(SEND_GAP_S)

    async def close_card(self, hold: Hold) -> None:
        message_id = self.messages.pop(hold.hold_id, None)
        if message_id is None:
            return
        async with self._send_lock:
            await self._app.bot.edit_message_text(
                chat_id=self.owner_id,
                message_id=message_id,
                text=card_html(hold, prompted_by(self.runtime, hold)) + outcome_line([hold.state]),
                parse_mode="HTML",
            )
            await asyncio.sleep(SEND_GAP_S)

    # The morning brief (Phase 3) ---------------------------------------------------------------

    async def send_brief(self, text: str, facts: BriefFacts) -> None:
        """The brief as one message. Its question gets Approve and Deny, unless a card for one
        of its calls is already open: then the brief points to that card, so nothing is asked
        twice."""
        if self._app is None:
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        markup = None
        lines = [brief_html(text)]
        question = facts.question
        carded = (
            [h.hold_id for h in question.holds if h.hold_id in self.messages] if question else []
        )
        if question is not None and carded:
            times = sorted({self.card_times.get(h, "") for h in carded} - {""})
            sent = f" sent at {', '.join(times)}" if times else ""
            plural = "s" if len(carded) > 1 else ""
            lines.append(f"\U0001f449 Answer it on the card{plural}{sent}.")
        elif question is not None:
            # A brief's question stays answerable all morning (until the holds expire).
            ttl = self.runtime.settings.policy.holds.expires_hours * 3600
            rows = []
            for choice, label in ((question.approve, "Approve"), (question.deny, "Deny")):
                token = secrets.token_urlsafe(9)
                self.questions[token] = QuestionToken(
                    [(h.hold_id, choice, h.fingerprint) for h in question.holds],
                    time.monotonic() + ttl,
                )
                rows.append(InlineKeyboardButton(label, callback_data=f"q:{token}"))
            markup = InlineKeyboardMarkup([rows])
        body = "\n\n".join(lines)
        async with self._send_lock:
            message = await self._app.bot.send_message(
                chat_id=self.owner_id,
                text=body,
                parse_mode="HTML",
                reply_markup=markup,
            )
            if question is not None:
                self.briefs[message.message_id] = BriefMessage(
                    body, tuple(h.hold_id for h in question.holds)
                )
            await asyncio.sleep(SEND_GAP_S)
        skip = {h.hold_id for h in question.holds} if question else set()
        await self.send_overnight(skip)

    async def send_overnight(self, skip: set[str]) -> None:
        """After the brief: the calls held in the quiet hours that still wait, as cards. Calls of
        one agent held by the same rule share one card with one button per choice."""
        ttl = self.runtime.settings.policy.holds.expires_hours * 3600
        groups: dict[tuple[str, str], list[Hold]] = {}
        for hold in self.runtime.holds.pending():
            if hold.hold_id in skip or hold.hold_id in self.messages or not self.quiet(hold):
                continue
            groups.setdefault((hold.agent, hold.rule), []).append(hold)
        for holds in groups.values():
            if len(holds) == 1:
                await self.send_card(holds[0], ttl)
            else:
                await self.send_group(holds, ttl)

    async def send_group(self, holds: list[Hold], ttl_s: float) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        row = []
        for choice in holds[0].choices:
            token = secrets.token_urlsafe(9)
            self.questions[token] = QuestionToken(
                [(h.hold_id, choice["id"], h.fingerprint) for h in holds],
                time.monotonic() + ttl_s,
            )
            label = f"{choice['label']} (all {len(holds)})"
            row.append(InlineKeyboardButton(label, callback_data=f"q:{token}"))
        text = group_html(holds, prompted_by(self.runtime, holds[0]))
        async with self._send_lock:
            message = await self._app.bot.send_message(
                chat_id=self.owner_id,
                text=text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([row]),
            )
            self.briefs[message.message_id] = BriefMessage(text, tuple(h.hold_id for h in holds))
            await asyncio.sleep(SEND_GAP_S)

    async def close_briefs(self, hold: Hold) -> None:
        """Once every call in a brief's question is decided (anywhere), say so on the brief and
        drop its buttons."""
        for message_id, brief in list(self.briefs.items()):
            if hold.hold_id not in brief.hold_ids:
                continue
            found = [self.runtime.holds.get(h) for h in brief.hold_ids]
            states = [h.state if h is not None else "unknown" for h in found]
            if "pending" in states:
                continue
            self.briefs.pop(message_id, None)
            async with self._send_lock:
                await self._app.bot.edit_message_text(
                    chat_id=self.owner_id,
                    message_id=message_id,
                    text=brief.text + outcome_line(states),
                    parse_mode="HTML",
                )
                await asyncio.sleep(SEND_GAP_S)

    async def handle_question(self, data: str) -> str:
        token = data.removeprefix("q:")
        question = self.questions.get(token)
        if question is None or question.expires < time.monotonic():
            return "This button has expired. Use the console."
        outcomes = []
        for hold_id, choice, fingerprint in question.decisions:
            try:
                decided = await self.runtime.decide(
                    hold_id, choice, fingerprint, "owner (telegram brief)"
                )
                outcomes.append(decided.state)
            except HoldError as exc:
                outcomes.append(exc.message)
        for key in [k for k, q in self.questions.items() if q.decisions == question.decisions]:
            self.questions.pop(key, None)
        return "Done: " + "; ".join(outcomes)

    # Presses ----------------------------------------------------------------------------------

    async def _on_query(self, update: Any, context: Any) -> None:
        query = update.callback_query
        if query is None:
            return
        outcome = await self.handle_press(query.from_user.id, str(query.data or ""))
        await query.answer(outcome[:190])

    async def handle_press(self, user_id: int, data: str) -> str:
        """Decide a hold from a button press; returns the short answer shown to the owner."""
        if user_id != self.owner_id:
            return "Not allowed."
        if data.startswith("q:"):
            return await self.handle_question(data)
        token = data.removeprefix("d:")
        button = self.buttons.get(token)
        if button is None or button.expires < time.monotonic():
            return "This button has expired. Use the console."
        hold = self.runtime.holds.get(button.hold_id)
        if hold is None:
            return "No such hold."
        try:
            decided = await self.runtime.decide(
                hold.hold_id, button.choice, hold.fingerprint, "owner (telegram)"
            )
        except HoldError as exc:
            return exc.message
        for key in [k for k, b in self.buttons.items() if b.hold_id == hold.hold_id]:
            self.buttons.pop(key, None)
        return f"Done: the hold is now {decided.state}."
