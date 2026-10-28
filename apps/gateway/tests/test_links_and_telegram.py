"""Signed approval links and Telegram button presses (P2-D10)."""

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from gateway_helpers import Upstream, call, make_settings, story_clock
from itsdangerous import URLSafeTimedSerializer

from steward_gateway.adapters import MockAdapter
from steward_gateway.app import create_app
from steward_gateway.links import SALT, make_link
from steward_gateway.runtime import Runtime
from steward_gateway.telegram_cards import TelegramCards, card_html

SECRET = "stw_test_approval_secret_value"
SEND = {"to": "a@b.example", "subject": "Thursday", "body": "See you <b>then</b> & after"}


async def quiet(request_id: str, message: str) -> None:
    return None


def app_with_secret(tmp_path: Path) -> Any:
    settings = replace(make_settings(tmp_path), approval_secret=SECRET)
    return create_app(
        settings,
        transport=httpx.MockTransport(Upstream()),
        adapters={"personal": MockAdapter(quiet), "work": MockAdapter(quiet)},
        clock=story_clock(),
    )


async def held_send(rt: Runtime) -> str:
    held = await call(rt, "work", "mail_send_message", SEND)
    return str(held.structured_content["hold_id"])


def test_a_signed_link_confirms_then_decides(tmp_path: Path) -> None:
    async def scenario() -> tuple[Any, ...]:
        app = app_with_secret(tmp_path)
        rt: Runtime = app.state.steward
        async with app.router.lifespan_context(app):
            hold_id = await held_send(rt)
            hold = rt.holds.get(hold_id)
            assert hold is not None
            link = make_link(rt, hold, "deny")
            assert link is not None and link.startswith("http://localhost:8000/a/")
            path = link.removeprefix("http://localhost:8000")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as client:
                page = await client.get(path)
                state_after_get = rt.holds.get(hold_id).state  # type: ignore[union-attr]
                decided = await client.post(path)
                forged = await client.get("/a/not-a-real-token")
                other = URLSafeTimedSerializer("another-secret", salt=SALT).dumps(
                    {"hold_id": hold_id, "fingerprint": hold.fingerprint, "choice": "approve"}
                )
                wrong_secret = await client.get(f"/a/{other}")
            return page, state_after_get, decided, forged, wrong_secret, rt.holds.get(hold_id)

    page, state_after_get, decided, forged, wrong_secret, hold = asyncio.run(scenario())
    # Opening the link only shows a page; a link preview cannot decide anything.
    assert page.status_code == 200 and "Deny" in page.text and state_after_get == "pending"
    assert decided.status_code == 200 and hold.state == "denied"
    assert forged.status_code == 403 and wrong_secret.status_code == 403


def test_links_are_off_without_a_secret(tmp_path: Path) -> None:
    async def scenario() -> tuple[Any, int]:
        app = create_app(
            make_settings(tmp_path),
            transport=httpx.MockTransport(Upstream()),
            adapters={"personal": MockAdapter(quiet), "work": MockAdapter(quiet)},
        )
        rt: Runtime = app.state.steward
        async with app.router.lifespan_context(app):
            hold = rt.holds.get(await held_send(rt))
            assert hold is not None
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as client:
                status = (await client.get("/a/anything")).status_code
            return make_link(rt, hold, "approve"), status

    link, status = asyncio.run(scenario())
    assert link is None and status == 404


def test_telegram_presses_decide_only_for_the_owner(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, ...]:
        app = app_with_secret(tmp_path)
        rt: Runtime = app.state.steward
        async with app.router.lifespan_context(app):
            cards = TelegramCards("123:fake-token", 42, rt)
            hold = rt.holds.get(await held_send(rt))
            assert hold is not None
            html = card_html(hold)
            deny = cards.button_token(hold, "deny")
            stranger = await cards.handle_press(7, f"d:{deny}")
            owner = await cards.handle_press(42, f"d:{deny}")
            again = await cards.handle_press(42, f"d:{deny}")
            second = rt.holds.get(await held_send(rt))
            assert second is not None
            stale = cards.button_token(second, "approve")
            cards.buttons[stale].expires = time.monotonic() - 1
            expired = await cards.handle_press(42, f"d:{stale}")
            return html, stranger, owner, again, expired, rt.holds.get(hold.hold_id).state  # type: ignore[union-attr]

    html, stranger, owner, again, expired, state = asyncio.run(scenario())
    # The card escapes what the agent wrote, so it cannot inject Telegram HTML.
    assert "&lt;b&gt;then&lt;/b&gt; &amp; after" in html and "<b>then</b>" not in html
    assert "Locked to:" in html
    assert stranger == "Not allowed."
    assert owner == "Done: the hold is now denied." and state == "denied"
    assert again.startswith("This button has expired")
    assert expired.startswith("This button has expired")


def test_request_urls_with_the_bot_token_are_not_logged() -> None:
    """The Bot API puts the token in the URL; httpx must not log request URLs at INFO."""
    import logging

    import steward_gateway.telegram_cards  # noqa: F401

    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
