"""Quiet hours and the brief's one question in Telegram: nothing is asked twice (Phase 3)."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from gateway_helpers import call, running, story_clock

from steward_fixtures import at, resolve_anchor
from steward_gateway import telegram_cards
from steward_gateway.telegram_cards import TelegramCards, in_quiet_hours


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=len(self.sent))

    async def edit_message_text(self, **kwargs: Any) -> None:
        self.edited.append(kwargs)


def story(day: int, hhmm: str) -> datetime:
    return at(resolve_anchor("replay"), day, hhmm)


def test_quiet_hours_run_across_midnight() -> None:
    def quiet(hhmm: str) -> bool:
        # 12 Oct 2026 is in British Summer Time: London is UTC+1.
        hour, minute = (int(x) for x in hhmm.split(":"))
        moment = datetime(2026, 10, 12, hour, minute, tzinfo=UTC).replace(hour=(hour - 1) % 24)
        return in_quiet_hours(moment, "22:00", "07:30")

    assert [quiet(t) for t in ("21:59", "22:00", "03:00", "07:29", "07:30", "12:00")] == [
        False,
        True,
        True,
        True,
        False,
        False,
    ]
    assert not in_quiet_hours(story(0, "03:00"), None, "07:30")


def test_a_held_call_is_asked_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_cards, "SEND_GAP_S", 0)

    async def scenario() -> dict[str, Any]:
        clock = story_clock(-1, "21:00")
        async with running(tmp_path, clock=clock) as (_, rt, _):
            assert rt.scheduler is not None
            bot = FakeBot()
            cards = TelegramCards("1:fake", 42, rt)
            cards._app = SimpleNamespace(bot=bot)
            rt.holds.listeners.append(cards.on_hold)
            rt.extras["telegram"] = cards
            out: dict[str, Any] = {}

            # 21:00: a card at once (a hold waits 12 hours, so it is still open at 07:30). 23:30,
            # in the quiet hours: no card.
            evening = await call(rt, "work", "todo_delete_item", {"id": "todo-pack"})
            clock.set(story(-1, "23:30"), rate=0)
            night = await call(rt, "work", "todo_delete_item", {"id": "todo-expenses"})
            await asyncio.sleep(0.05)
            out["cards"] = len(bot.sent)

            # 07:30: the question is the oldest waiting call, which has a card: no buttons.
            clock.set(story(0, "07:30"), rate=0)
            await rt.scheduler.run_job("brief", clock.now())
            out["first_brief"] = bot.sent[-1]

            # Deny on the card: the card and the brief both say so.
            hold_id = evening.structured_content["hold_id"]
            hold = rt.holds.get(hold_id)
            assert hold is not None
            await cards.handle_press(42, f"d:{cards.button_token(hold, 'deny')}")
            await asyncio.sleep(0.05)
            out["edited_after_card"] = list(bot.edited)

            # A second brief asks about the night's call, which never had a card: buttons.
            clock.set(story(0, "07:31"), rate=0)
            await rt.scheduler.run_job("brief", clock.now())
            second = bot.sent[-1]
            out["second_brief"] = second
            deny = second["reply_markup"].inline_keyboard[0][1].callback_data
            out["press"] = await cards.handle_press(42, deny)
            await asyncio.sleep(0.05)
            out["night_state"] = rt.holds.get(night.structured_content["hold_id"]).state  # type: ignore[union-attr]
            out["edited"] = list(bot.edited)
            return out

    out = asyncio.run(scenario())
    assert out["cards"] == 1
    first = out["first_brief"]
    assert first["reply_markup"] is None and "Answer it on the card sent at 21:00." in first["text"]
    texts = [e["text"] for e in out["edited_after_card"]]
    assert any("<b>Now:</b> denied" in t for t in texts)
    assert any(t.startswith("<b>Morning brief</b>") and "Answered:</b> denied" in t for t in texts)
    assert out["second_brief"]["reply_markup"] is not None
    assert out["press"] == "Done: denied" and out["night_state"] == "denied"
    assert sum("Answered:</b> denied" in e["text"] for e in out["edited"]) == 2
