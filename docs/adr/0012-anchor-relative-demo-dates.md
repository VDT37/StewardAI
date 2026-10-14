# 0012. Anchor-relative demo dates

Status: Accepted (2026-10-14)

## Context

The demo must stay up until judging ends on 15 December. Fixture data with calendar dates in October would look stale by then. The recorded video, the replay and the trap suite also need the same dates on every run, or their outputs would differ (ADR 0004).

## Decision

Fixture data stores day offsets from an anchor Monday (day 0) and local times, never calendar dates. A loader turns offsets into dates when the data is read.

`STEWARD_DEMO_ANCHOR` selects the anchor:

| Value | Day 0 | Used for |
| --- | --- | --- |
| `replay` | Mon 12 Oct 2026 | Replays, the trap suite and the video, which stay byte-for-byte repeatable |
| `next-monday` | The first Monday after today | The playground and live mode, so judges in December still see upcoming dates |
| An ISO date | That date, which must be a Monday | Pinning any other week |

Times are local, Europe/London. This matters across the UK clock change on Sun 25 Oct 2026.

All names are fictional and all email addresses use `.example` domains. A planted-details registry lists every private string that must never cross, and the property test reads it (ADR 0002).

Example: the cardiology appointment is stored as day 2, 14:15 to 15:45. In replay that is Wed 14 Oct. A judge who opens the playground on Wed 2 Dec gets day 0 = Mon 7 Dec, so the same appointment shows as Wed 9 Dec.

## Consequences

- One data set serves both the replay and the live demo.
- The loader must handle time zones. A test checks that 09:30 on day 0 has a UTC offset of +1 hour (BST) and that 09:30 on day 14 (Mon 26 Oct, after the clocks go back) has an offset of 0.
- Recurring events list every day they occur. Weekday events cover four weeks (days 0 to 4, 7 to 11, 14 to 18 and 21 to 25), so the fixtures run out after day 25.
- The loader rejects an ISO date that is not a Monday.
- Fixtures need a small loader and tests, which come in Phase 0.

## Alternatives considered

Fixed October dates. The demo looks stale in December.

Shifting dates by hand. Easy to get wrong.

Faking the clock. Also easy to get wrong.
