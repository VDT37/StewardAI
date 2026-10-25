"""The tool-call rules: classes, the trifecta, stand-ins, the trip limit and stale writes."""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

from steward_core.address import ADDRESS_TOOLS, StandIn
from steward_core.checker import Decision
from steward_core.ids import IdMint
from steward_core.policy import load_policy
from steward_core.rules import CallFacts, FlagBook, decide

ROOT = Path(__file__).resolve().parents[3]
POLICY = load_policy(ROOT / "config" / "steward.yaml")
NOW = dt.datetime(2026, 10, 11, 19, 5, tzinfo=dt.UTC)


def facts(tool_id: str, arguments: dict[str, Any] | None = None, **extra: Any) -> CallFacts:
    return CallFacts(
        agent="work",
        tool_id=tool_id,
        rule=POLICY.tool_rule(tool_id),
        arguments=arguments or {},
        now=NOW,
        **extra,
    )


def stand_in(covers: int = 1, **extra: Any) -> StandIn:
    data: dict[str, Any] = {
        "token": "[ADDR-1]",
        "agent": "work",
        "hold_id": "h-1",
        "tools": ADDRESS_TOOLS,
        "trip_date": "2026-10-15",
        "expires": "2026-10-12T18:06:00.000Z",
        "covers": covers,
        **extra,
    }
    return StandIn(**data)


def test_reads_are_allowed_and_class_rules_hold() -> None:
    assert decide(facts("work_calendar.list_events")).decision is Decision.ALLOW
    send = decide(facts("work_mail.send_message", {"to": "a@b.example"}))
    assert send.decision is Decision.HOLD and send.rule == "send_needs_owner"
    assert decide(facts("shared_todo.delete_item")).rule == "destructive_needs_owner"
    assert decide(facts("travel.change_booking", {"booking_id": "bk-1"})).rule == (
        "paid_change_needs_owner"
    )


def test_trifecta_holds_egress_only_with_both_flags() -> None:
    page = {"url": "https://manchester-station-guide.example/collect"}
    one_flag = decide(facts("web.extract", page, private_read=True))
    assert one_flag.decision is Decision.ALLOW
    both = decide(facts("web.extract", page, private_read=True, untrusted_seen=True))
    assert both.decision is Decision.HOLD and both.rule == "trifecta"
    write = decide(
        facts(
            "shared_todo.update_item",
            {"id": "todo-pack", "fields": {"done": True}, "expected_version": 1},
            private_read=True,
            untrusted_seen=True,
            current_version=1,
        )
    )
    assert write.decision is Decision.ALLOW
    train = decide(
        facts(
            "travel.book_train",
            {"option_id": "train-0930"},
            private_read=True,
            untrusted_seen=True,
            price_gbp=Decimal(89),
            trip_date="2026-10-15",
            trip_total_gbp=Decimal(0),
            trip_limit_gbp=Decimal(300),
        )
    )
    assert train.decision is Decision.HOLD and train.rules == ("trifecta",)


def test_send_after_both_flags_names_both_rules() -> None:
    verdict = decide(facts("work_mail.send_message", private_read=True, untrusted_seen=True))
    assert verdict.rules == ("send_needs_owner", "trifecta")


def test_spend_over_the_trip_limit_is_held() -> None:
    verdict = decide(
        facts(
            "travel.book_train",
            {"option_id": "train-0930"},
            price_gbp=Decimal(89),
            trip_date="2026-10-15",
            trip_total_gbp=Decimal(250),
            trip_limit_gbp=Decimal(300),
        )
    )
    assert verdict.decision is Decision.HOLD and verdict.rule == "trip_limit"


def test_stale_write_is_held() -> None:
    args = {"id": "todo-expenses", "fields": {"title": "x"}, "expected_version": 1}
    verdict = decide(facts("shared_todo.update_item", args, current_version=2))
    assert verdict.decision is Decision.HOLD and verdict.rule == "stale_write"


def test_the_address_approval_covers_one_taxi_booking() -> None:
    args = {"date": "2026-10-15", "pickup_time": "09:00", "address": "[ADDR-1]"}
    common: dict[str, Any] = {
        "private_read": True,
        "untrusted_seen": True,
        "price_gbp": Decimal(14),
        "trip_date": "2026-10-15",
        "trip_total_gbp": Decimal(89),
        "trip_limit_gbp": Decimal(300),
    }
    covered = decide(facts("travel.book_taxi", args, stand_ins={"[ADDR-1]": stand_in()}, **common))
    assert covered.decision is Decision.ALLOW and covered.covered_by == "[ADDR-1]"
    used = decide(
        facts("travel.book_taxi", args, stand_ins={"[ADDR-1]": stand_in(covers=0)}, **common)
    )
    assert used.decision is Decision.REFUSE and used.rule == "stand_in_used"


def test_stand_ins_anywhere_else_are_refused() -> None:
    binding = {"[ADDR-1]": stand_in()}
    email = decide(
        facts("work_mail.send_message", {"body": "pick up at [ADDR-1]"}, stand_ins=binding)
    )
    assert email.decision is Decision.REFUSE and email.rule == "stand_in_misuse"
    search = decide(facts("web.search", {"query": "taxi from [ADDR-1]"}, stand_ins=binding))
    assert search.rule == "stand_in_misuse"
    unknown = decide(
        facts("travel.book_taxi", {"address": "[ADDR-7]"}, stand_ins={"[ADDR-7]": None})
    )
    assert unknown.rule == "stand_in_unknown"
    other_trip = decide(
        facts(
            "travel.book_taxi",
            {"date": "2026-10-16", "pickup_time": "09:00", "address": "[ADDR-1]"},
            stand_ins=binding,
            trip_date="2026-10-16",
        )
    )
    assert other_trip.rule == "stand_in_misuse"
    plain = decide(facts("travel.book_taxi", {"address": "1 Office Street"}))
    assert plain.rule == "stand_in_required"


def test_flags_clear_at_four_in_the_morning() -> None:
    flags = FlagBook()
    sunday_evening = dt.datetime(2026, 10, 11, 18, 3, tzinfo=dt.UTC)
    assert flags.set("work", "untrusted_seen", "web.extract:page", sunday_evening)
    assert not flags.set("work", "untrusted_seen", "work_mail:x", sunday_evening)
    seen, cleared = flags.read("work", dt.datetime(2026, 10, 12, 2, 59, tzinfo=dt.UTC))
    assert seen.has("untrusted_seen") and not cleared
    # 04:00 BST on Mon 12 Oct is 03:00 UTC.
    after, cleared = flags.read("work", dt.datetime(2026, 10, 12, 3, 0, tzinfo=dt.UTC))
    assert cleared and not after.has("untrusted_seen")


def test_ids_are_deterministic_per_session() -> None:
    one, two = IdMint("s-1"), IdMint("s-1")
    assert [one.next("h"), one.next("h"), one.next("r")] == [
        two.next("h"),
        two.next("h"),
        two.next("r"),
    ]
    assert IdMint("s-2").next("h") != IdMint("s-1").next("h")
    assert one.number("ADDR") == 1 and one.number("ADDR") == 2
