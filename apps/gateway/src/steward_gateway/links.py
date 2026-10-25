"""Signed approval links (P2-D10).

A link carries {hold_id, fingerprint, choice}, signed with STEWARD_APPROVAL_SECRET by
itsdangerous's URLSafeTimedSerializer, and stops working after holds.link_minutes. GET only shows
a confirmation page, because link previews and prefetchers open links on their own; the POST
from that page decides. The fingerprint inside the link must still match the held call, so a
link for an older version of a call does nothing. Links are off without the secret.
"""

from __future__ import annotations

import html
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from steward_core.store import Hold
from steward_gateway.holds import HoldError, short_fingerprint
from steward_gateway.runtime import Runtime

SALT = "steward-approval"


def serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=SALT)


def make_link(runtime: Runtime, hold: Hold, choice: str) -> str | None:
    secret = runtime.settings.approval_secret
    if not secret:
        return None
    token = serializer(secret).dumps(
        {"hold_id": hold.hold_id, "fingerprint": hold.fingerprint, "choice": choice}
    )
    return f"{runtime.settings.public_url.rstrip('/')}/a/{token}"


def read_link(runtime: Runtime, token: str) -> dict[str, Any]:
    secret = runtime.settings.approval_secret
    if not secret:
        raise HTTPException(404, "approval links are off")
    max_age = runtime.settings.policy.holds.link_minutes * 60
    try:
        data = serializer(secret).loads(token, max_age=max_age)
    except SignatureExpired as exc:
        raise HTTPException(410, "this link has expired") from exc
    except BadSignature as exc:
        raise HTTPException(403, "this link is not valid") from exc
    if not isinstance(data, dict) or not {"hold_id", "fingerprint", "choice"} <= set(data):
        raise HTTPException(403, "this link is not valid")
    return data


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><meta name='viewport' "
        "content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<body style='font:16px/1.5 system-ui;max-width:36rem;margin:2rem auto;padding:0 1rem'>"
        f"<h1 style='font-size:1.3rem'>{html.escape(title)}</h1>{body}</body>"
    )


def links_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/a/{token}")
    async def confirm(token: str) -> HTMLResponse:
        data = read_link(runtime, token)
        hold = runtime.holds.get(str(data["hold_id"]))
        if hold is None:
            raise HTTPException(404, "no such hold")
        label = next((c["label"] for c in hold.choices if c["id"] == data["choice"]), "Decide")
        body = (
            f"<p>{html.escape(hold.reason)}</p>"
            f"<p><b>{html.escape(hold.agent)}</b> agent, <code>{html.escape(hold.tool)}</code>, "
            f"fingerprint <code>{short_fingerprint(hold.fingerprint)}</code>, "
            f"state {html.escape(hold.state)}.</p>"
            f"<form method='post'><button style='font-size:1rem;padding:.5rem 1rem'>"
            f"{html.escape(label)}</button></form>"
        )
        return _page("Steward approval", body)

    @router.post("/a/{token}")
    async def decide(token: str) -> HTMLResponse:
        data = read_link(runtime, token)
        try:
            hold = await runtime.decide(
                str(data["hold_id"]), str(data["choice"]), str(data["fingerprint"]), "owner (link)"
            )
        except HoldError as exc:
            return _page("Nothing was changed", f"<p>{html.escape(exc.message)}</p>")
        return _page("Done", f"<p>The hold is now {html.escape(hold.state)}.</p>")

    return router
