"""The morning brief's facts, its template and the check on model-written text (Phase 3, P3-D6).

Code picks the facts. They are typed values only (counts, times, amounts, what was held,
refused or trimmed, change notices, suspected phishing, watcher findings), never an agent's own
words or a tool's output, so nothing an attacker wrote can reach the model that writes the
sentences. The helper model turns the facts into a few short lines; then code checks those lines:
every time, number and weekday must appear in the facts, no planted detail or address may
appear, and the text has a length cap. If anything fails, or the model cannot be reached, the
template writes the brief instead. The one question is always written by code, so the model
can never misstate what the owner approves.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
SHORT_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# A clock time; never an amount such as $0.50 or £14.00.
TIME = re.compile(r"(?<![$£\d.,])\b\d{1,2}[:.]\d{2}\b")
AMPM = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", re.IGNORECASE)
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# A JSON key such as calls_held_for_owner copied into the text.
FIELD_NAME = re.compile(r"\b[a-z]+_[a-z_]+\b")
WORD = re.compile(r"[a-z]+")


class _Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentNight(_Fact):
    agent: str
    model: str
    model_calls: int
    tool_calls: int
    asks: int
    answered: int
    refused: int
    held: int
    spend_usd: str
    budget_usd: str
    spend_percent: int


class Booking(_Fact):
    kind: str  # train or taxi
    date: str  # "Thu 15 Oct"
    time: str  # departure or pickup, HH:MM
    price_gbp: str
    changed: bool
    stand_in: bool


class Refused(_Fact):
    when: str
    agent: str
    what: str  # the request type or tool, as code names it


class Notice(_Fact):
    when: str
    date: str
    start: str
    end: str
    can_move: bool


class StaleWrite(_Fact):
    when: str
    item: str
    state: str


class Mail(_Fact):
    when: str
    agent: str
    sender_domain: str
    subject: str
    signs: int
    links_withheld: int
    reported_by_agent: bool
    owner_verdict: str | None


class Pause(_Fact):
    when: str
    agent: str
    rule: str
    detail: str
    still_paused: bool


class QuestionHold(_Fact):
    hold_id: str
    fingerprint: str
    tool: str
    choices: tuple[tuple[str, str], ...]


class Question(_Fact):
    kind: str  # change_paid, stale_write or other
    text: str
    holds: tuple[QuestionHold, ...]
    approve: str  # the choice id that says yes for every hold in the question
    deny: str


class BriefFacts(_Fact):
    brief_id: str
    date: str  # "Mon 12 Oct"
    since: str  # "Sun 11 Oct 19:00"
    until: str  # "Mon 12 Oct 07:30"
    agents: tuple[AgentNight, ...]
    bookings: tuple[Booking, ...]
    refused: tuple[Refused, ...]
    asks_held: int
    notices: tuple[Notice, ...]
    stale_writes: tuple[StaleWrite, ...]
    mail: tuple[Mail, ...]
    pauses: tuple[Pause, ...]
    pending: int
    question: Question | None
    chain_ok: bool

    def for_model(self) -> dict[str, Any]:
        """What the helper model sees: the facts without ids, fingerprints or the question, and
        without the few strings someone else wrote (an email's subject, a to-do item's title,
        the request type a refused ask named), so no attacker text reaches the writer. The
        template, written by code, still quotes them."""
        data = self.model_dump(mode="json", exclude={"brief_id", "question"})
        for mail in data["mail"]:
            mail.pop("subject", None)
        for stale in data["stale_writes"]:
            stale.pop("item", None)
        for refused in data["refused"]:
            refused.pop("what", None)
        data["refused_calls"] = data.pop("refused")
        data["asks_held_by_daily_limit_or_burst"] = data.pop("asks_held")
        data["calls_waiting_for_owner"] = data.pop("pending")
        # Names that say what each count is, so a held call is not read as a held ask.
        for night in data["agents"]:
            for old, new in MODEL_NAMES.items():
                night[new] = night.pop(old)
        return data


# The per-agent counts as the helper model sees them.
MODEL_NAMES = {
    "asks": "asks_to_the_other_agent",
    "answered": "requests_answered",
    "refused": "calls_refused",
    "held": "calls_held_for_owner",
}


# Template ---------------------------------------------------------------------------------------


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def summary_lines(facts: BriefFacts) -> list[str]:
    """The template: one line per topic, from the facts alone."""
    lines: list[str] = []
    by_agent = {a.agent: a for a in facts.agents}
    work = by_agent.get("work")
    if work is not None:
        bits = []
        trips = [b for b in facts.bookings if b.kind == "train"]
        taxis = [b for b in facts.bookings if b.kind == "taxi"]
        if trips:
            t = trips[0]
            moved = " (moved)" if t.changed else ""
            bits.append(f"train booked for {t.date} at {t.time}{moved}, £{t.price_gbp}")
        if taxis:
            x = taxis[0]
            stand = " using an address stand-in" if x.stand_in else ""
            bits.append(f"taxi pickup at {x.time}{stand}")
        refused = [r for r in facts.refused if r.agent == "work"]
        if refused:
            first = refused[0]
            bits.append(f"an ask for {first.what} at {first.when} was refused")
        if facts.asks_held:
            bits.append(f"{_count(facts.asks_held, 'ask')} held by the daily limit or burst check")
        line = "Work agent: " + (_join(bits) or "a quiet night")
        lines.append(line + ".")
    personal = by_agent.get("personal")
    if personal is not None:
        bits = [f"answered {_count(personal.answered, 'request')}"]
        if facts.notices:
            n = facts.notices[0]
            bits.append(
                f"Steward passed on a change notice at {n.when} ({n.date}, {n.start} to {n.end}, "
                "now busy)"
            )
        lines.append("Personal agent: " + _join(bits) + ".")
    for stale in facts.stale_writes:
        lines.append(
            f"Shared to-do list: a stale write to '{stale.item}' was held at {stale.when}; both "
            "versions are kept."
        )
    for mail in facts.mail:
        reported = " The work agent reported it." if mail.reported_by_agent else ""
        links = (
            f" Steward withheld {_count(mail.links_withheld, 'link')}."
            if mail.links_withheld
            else ""
        )
        lines.append(
            f"Mail: a suspected phishing email ('{mail.subject}' from {mail.sender_domain}) "
            f"arrived at {mail.when}.{links}{reported}"
        )
    if facts.pauses:
        for pause in facts.pauses:
            state = "still paused" if pause.still_paused else "resumed since"
            lines.append(
                f"Watcher: paused the {pause.agent} agent at {pause.when} ({pause.detail}); "
                f"{state}."
            )
    else:
        lines.append("Watcher: no agent hit a hard limit.")
    spend = [f"{a.agent} ${a.spend_usd} of ${a.budget_usd}" for a in facts.agents]
    if spend:
        lines.append("Spend: " + _join(spend) + ".")
    return lines


def question_line(facts: BriefFacts) -> str:
    if facts.question is None:
        return "No question this morning."
    more = facts.pending - len(facts.question.holds)
    tail = f" ({_count(more, 'more call')} waiting in the console.)" if more > 0 else ""
    return f"One question: {facts.question.text}{tail}"


def render_template(facts: BriefFacts) -> str:
    return "\n".join([*summary_lines(facts), question_line(facts)])


# Check ------------------------------------------------------------------------------------------


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif value is not None and not isinstance(value, bool):
        yield str(value)


def allowed_tokens(facts: BriefFacts) -> tuple[set[str], set[str], set[str]]:
    """(times, numbers, weekday words) that appear in the facts the model was given."""
    text = " ".join(_strings(facts.for_model())).lower()
    times = {_norm_time(t) for t in TIME.findall(text)}
    numbers = {_norm_number(n) for n in NUMBER.findall(text)}
    days = {w for w in WORD.findall(text) if w in WEEKDAYS or w in SHORT_DAYS}
    # A short weekday in the facts allows its full name, and the other way round.
    for i, short in enumerate(SHORT_DAYS):
        if short in days or WEEKDAYS[i] in days:
            days |= {short, WEEKDAYS[i]}
    return times, numbers, days


def _norm_time(value: str) -> str:
    hours, _, minutes = value.replace(".", ":").partition(":")
    return f"{int(hours):02d}:{minutes}"


def _norm_number(value: str) -> str:
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value.lstrip("0") or "0"


def check_text(
    text: str,
    facts: BriefFacts,
    *,
    forbidden: Iterable[str] = (),
    max_chars: int = 900,
) -> list[str]:
    """Problems with the model's summary; an empty list means it may be used."""
    problems: list[str] = []
    if not text.strip():
        return ["the text is empty"]
    if len(text) > max_chars:
        problems.append(f"longer than {max_chars} characters")
    lowered = text.lower()
    if "http" in lowered or "www." in lowered:
        problems.append("contains a link")
    if "?" in text:
        problems.append("asks a question (only code writes the question)")
    if FIELD_NAME.search(text):
        problems.append("names a field instead of saying what it means")
    if AMPM.search(text):
        problems.append("uses am or pm instead of the 24-hour times in the facts")
    times, numbers, days = allowed_tokens(facts)
    found_times = TIME.findall(text)
    for value in found_times:
        if _norm_time(value) not in times:
            problems.append(f"time {value} is not in the facts")
    without_times = TIME.sub(" ", text)
    for value in NUMBER.findall(without_times):
        if _norm_number(value) not in numbers:
            problems.append(f"number {value} is not in the facts")
    for word in WORD.findall(lowered):
        if (word in WEEKDAYS or word in SHORT_DAYS) and word not in days:
            problems.append(f"weekday {word} is not in the facts")
    for item in forbidden:
        if item and item.lower() in lowered:
            problems.append("contains a private detail")
            break
    return problems


SYSTEM_PROMPT = (
    "You write the summary part of a morning brief for the owner of two AI agents (a work agent "
    "and a personal agent) that Steward, a gatekeeper, watched overnight. You get the night's "
    "facts as JSON. Write 4 to 6 short lines in plain, friendly British English, one line per "
    "topic, each starting with its topic and a colon, in this order: "
    "Work agent: what it booked (bookings: kind, date, time, price, whether an address stand-in "
    "was used), how many of its calls were refused (calls_refused), how many calls wait for the "
    "owner (calls_held_for_owner), and how many of its asks the daily limit or burst check held "
    "(asks_held_by_daily_limit_or_burst). Leave out any count that is 0. "
    "Personal agent: how many requests it answered (requests_answered) and any change notice "
    "Steward passed on (notices: when, date, start to end). "
    "Shared to-do list: only if stale_writes is not empty, say a write made from an old copy "
    "was held and when. "
    "Mail: one line per entry in mail: when it arrived, the sender domain, how many links "
    "Steward withheld, and whether the agent reported it. Leave this line out if mail is empty. "
    "Watcher: each pause, or 'no agent hit a hard limit' when pauses is empty. "
    "Spend: each agent's spend_usd of its budget_usd, in US dollars with a $ sign and every "
    "digit as given, for example $0.1373 (booking prices are pounds, with a £ sign). Put each "
    "topic on its own line. "
    "Use only the facts. Never count, add up or work out a number yourself: copy every time, "
    "date, amount and number exactly as written in the facts (24-hour times such as 07:30, "
    "dates such as Thu 15 Oct). Do not name JSON keys, do not invent anything, do not add "
    "advice, do not ask a question, no markdown, no links. A question for the owner is added "
    "after your lines by code, so do not mention calls waiting."
)
