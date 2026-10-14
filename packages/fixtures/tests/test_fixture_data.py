import json
import re
from typing import Any

import pytest

from steward_fixtures import FIXTURE_FILES, load_raw, planted_details

PERSONAL_FILES = ("personal_calendar", "personal_gmail", "needs_profile")
WORK_FILES = ("work_calendar", "work_mail")
SHARED_FILES = ("shared_todo", "web_pages", "travel_options")


def _text(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False).lower()


def _blob(names: tuple[str, ...]) -> str:
    return " ".join(_text(load_raw(n)) for n in names)


@pytest.mark.parametrize("name", FIXTURE_FILES)
def test_every_fixture_file_loads(name: str) -> None:
    assert load_raw(name)


def test_every_planted_text_occurs_in_the_fixture_data() -> None:
    blob = _blob(tuple(n for n in FIXTURE_FILES if n != "planted"))
    missing = [d["text"] for d in planted_details() if d["text"].lower() not in blob]
    assert missing == []


def test_planted_ids_are_unique() -> None:
    ids = [d["id"] for d in planted_details()]
    assert len(ids) == len(set(ids))


def test_planted_details_stay_on_their_own_side() -> None:
    script = load_raw("overnight_script")["changes"]
    personal_script = [c for c in script if c["side"] == "personal"]
    other_script = [c for c in script if c["side"] != "personal"]
    not_personal = _blob(WORK_FILES + SHARED_FILES) + _text(other_script)
    not_work = _blob(PERSONAL_FILES + SHARED_FILES) + _text(personal_script)
    leaks = [d["text"] for d in planted_details("personal") if d["text"].lower() in not_personal]
    leaks += [d["text"] for d in planted_details("work") if d["text"].lower() in not_work]
    assert leaks == []


def test_every_email_address_and_url_uses_an_example_domain() -> None:
    blob = _blob(FIXTURE_FILES)
    emails = re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", blob)
    hosts = re.findall(r"https?://([\w.-]+)", blob)
    assert emails and hosts
    assert [h for h in emails + hosts if not h.endswith(".example")] == []


def test_event_and_message_ids_are_unique() -> None:
    ids = [e["id"] for n in ("work_calendar", "personal_calendar") for e in load_raw(n)["events"]]
    ids += [m["id"] for n in ("work_mail", "personal_gmail") for m in load_raw(n)["messages"]]
    assert len(ids) == len(set(ids))
