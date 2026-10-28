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
decide each of them with its own stored fingerprint.
"""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from steward_core.brief import BriefFacts
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


def card_html(hold: Hold, prompted: str | None = None) -> str:
    """The card: what is held, the rule, what started the agent's run, the call and the
    fingerprint (HTML parse mode). The choices are the buttons, so they are not repeated."""
    what = hold.details.get("card") or f"The {hold.agent} agent called {hold.tool}."
    args = hold.args.get("arguments", hold.args) if isinstance(hold.args, dict) else hold.args
    lines = [
        f"<b>Held:</b> {html.escape(str(what))}",
        f"<b>Why:</b> {html.escape(hold.reason)}",
    ]
    if prompted:
        lines.append(f"<b>Prompted by:</b> {html.escape(prompted)}")
    lines += [
        f"<b>Call:</b> <code>{html.escape(hold.tool)}</code> "
        f"<code>{html.escape(str(args))[:300]}</code>",
        f"<b>Locked to:</b> fingerprint <code>{short_fingerprint(hold.fingerprint)}</code>. "
        "Any change voids the approval.",
    ]
    return "\n".join(lines)


class TelegramCards:
    def __init__(self, token: str, owner_id: int, runtime: Runtime) -> None:
        self.token = token
        self.owner_id = owner_id
        self.runtime = runtime
        self.buttons: dict[str, ButtonToken] = {}
        self.questions: dict[str, QuestionToken] = {}
        self.messages: dict[str, int] = {}
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

    def on_hold(self, hold: Hold, event: str) -> None:
        if self._app is None:
            return
        coro = self.send_card(hold) if event == "created" else self.close_card(hold)
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def button_token(self, hold: Hold, choice: str) -> str:
        token = secrets.token_urlsafe(9)
        ttl = self.runtime.settings.policy.holds.link_minutes * 60
        self.buttons[token] = ButtonToken(hold.hold_id, choice, time.monotonic() + ttl)
        return token

    async def send_card(self, hold: Hold) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        c["label"], callback_data=f"d:{self.button_token(hold, c['id'])}"
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
            await asyncio.sleep(SEND_GAP_S)

    async def close_card(self, hold: Hold) -> None:
        message_id = self.messages.pop(hold.hold_id, None)
        if message_id is None:
            return
        async with self._send_lock:
            await self._app.bot.edit_message_text(
                chat_id=self.owner_id,
                message_id=message_id,
                text=card_html(hold, prompted_by(self.runtime, hold))
                + f"\n\n<b>Now:</b> {html.escape(hold.state)}",
                parse_mode="HTML",
            )
            await asyncio.sleep(SEND_GAP_S)

    # The morning brief (Phase 3) ---------------------------------------------------------------

    async def send_brief(self, text: str, facts: BriefFacts) -> None:
        if self._app is None:
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        markup = None
        question = facts.question
        if question is not None:
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
        lines = [html.escape(line) for line in text.splitlines() if line.strip()]
        async with self._send_lock:
            await self._app.bot.send_message(
                chat_id=self.owner_id,
                text="<b>Morning brief</b>\n" + "\n".join(lines),
                parse_mode="HTML",
                reply_markup=markup,
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
