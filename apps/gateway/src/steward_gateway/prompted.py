"""What prompted a held call: the event that started the agent's run, from Steward's own records.

An approval card says what the agent wants and which rule held it. The owner also wants to know
why the agent was doing it at all. Steward starts or relays every run that goes through it: the
owner's request (a story beat in the Manchester evening), a change notice, an ask from the other
agent, or the owner's decision on an earlier held call that Steward tells the agent about. So
code takes the latest such event before the held call, for the same agent, in the same session
and within the hour. None of it comes from the agent's own words, and nothing is written to the
flight log, so replay is unaffected.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from steward_core.clock import LONDON, parse_iso
from steward_core.store import Event, Hold

if TYPE_CHECKING:
    from steward_gateway.runtime import Runtime

STARTERS = ("scenario.beat", "a2a.notice", "a2a.request", "hold.approved", "hold.denied")
HELD_KINDS = ("tool.held", "a2a.held")
# A run that started longer ago than this is not what the agent is working on.
WINDOW = timedelta(hours=1)
PROMPT_CHARS = 160
NOTHING = (
    "Nothing Steward relayed in the last hour started this run: the agent was working on its "
    "own, or someone spoke to it directly."
)
REQUEST_WORDS = {
    "availability": "availability",
    "travel_limits": "travel limits",
    "needs": "dietary and access needs",
    "address_for_booking": "an address stand-in",
}


def _hm(ts: str) -> str:
    return parse_iso(ts).astimezone(LONDON).strftime("%H:%M")


def _day(iso_date: str) -> str:
    try:
        return date.fromisoformat(iso_date).strftime("%a %d %b").replace(" 0", " ")
    except ValueError:
        return iso_date


def _held_seq(rt: Runtime, hold: Hold) -> int | None:
    """The seq of the event that created the hold (the held call itself)."""
    for kind in HELD_KINDS:
        for e in rt.store.events(kind=kind, session_id=hold.session_id, limit=500):
            if e.payload.get("hold_id") == hold.hold_id:
                return e.seq
    return None


def _starts(rt: Runtime, event: Event, hold: Hold) -> bool:
    """Whether this event started a run of the hold's agent."""
    p = event.payload
    if event.kind == "scenario.beat":
        if p.get("actor") == hold.agent and p.get("prompt"):
            return True
        # Scripted actors play an agent's part; the 22:41 tricked agent is the work agent.
        return p.get("actor") == "scripted" and p.get("agent", "work") == hold.agent
    if event.kind == "a2a.notice":
        return p.get("asker") == hold.agent
    if event.kind == "a2a.request":
        return p.get("contact") == hold.agent
    if event.kind in {"hold.approved", "hold.denied"}:
        # Steward tells the agent about a decision on a call of a notify class (a paid change).
        if event.agent != hold.agent or p.get("hold_id") == hold.hold_id:
            return False
        decided = rt.store.hold(str(p.get("hold_id")))
        policy = rt.settings.policy
        if decided is None or decided.tool_id is None or decided.tool_id not in policy.tools:
            return False
        return policy.tools[decided.tool_id].tool_class in policy.holds.notify_classes
    return False


def describe(event: Event) -> str:
    p: dict[str, Any] = event.payload
    when = _hm(event.ts)
    if event.kind == "scenario.beat":
        if p.get("actor") == "scripted":
            name = str(p.get("beat") or "").split(" ", 1)[-1]
            return f"the overnight script at {when} ({name}), a scripted actor, not the real agent"
        prompt = " ".join(str(p.get("prompt") or "").split())
        if len(prompt) > PROMPT_CHARS:
            prompt = prompt[: PROMPT_CHARS - 3].rstrip() + "..."
        if prompt.startswith("Steward:"):
            return f'Steward\'s note at {when}: "{prompt.removeprefix("Steward:").strip()}"'
        return f'your request at {when}: "{prompt}"'
    if event.kind == "a2a.notice":
        notices = p.get("notices") or []
        first = notices[0] if notices else {}
        state = "now busy" if first.get("change", "now_busy") == "now_busy" else "now free"
        more = f", and {len(notices) - 1} more" if len(notices) > 1 else ""
        return (
            f"Steward's change notice at {when} ({_day(str(first.get('date')))}, "
            f"{first.get('start')} to {first.get('end')}, {state}{more})"
        )
    if event.kind == "a2a.request":
        what = REQUEST_WORDS.get(str(p.get("request_type")), str(p.get("request_type")))
        return f"the {event.agent} agent's ask at {when} for {what}"
    verb = "approval" if event.kind == "hold.approved" else "refusal"
    return f"your {verb} at {when} of its held {p.get('tool')}"


def prompted_by(rt: Runtime, hold: Hold) -> str | None:
    """One sentence: what started the run in which the agent made this call."""
    if hold.kind == "pause":
        return None
    held = _held_seq(rt, hold)
    created = parse_iso(hold.created)
    latest: Event | None = None
    for kind in STARTERS:
        for e in rt.store.events(kind=kind, session_id=hold.session_id, limit=300):
            if held is not None and e.seq >= held:
                continue
            if held is None and e.ts > hold.created:
                continue
            if parse_iso(e.ts) < created - WINDOW:
                break
            if _starts(rt, e, hold):
                if latest is None or e.seq > latest.seq:
                    latest = e
                break
    if latest is None:
        return NOTHING
    text = describe(latest)
    return f"{text[0].upper()}{text[1:]}, which started this run."
