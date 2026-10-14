# 0002. Typed agent-to-agent protocol

Status: Accepted (2026-10-14)

## Context

Agents that coordinate go wrong in three ways.

- They overshare: one agent passes on details the other does not need.
- They can be tricked. A malicious email or web page (prompt injection) gets one agent to ask the other for private data. In the ConVerse benchmark (Gomaa, Salem and Abdelnabi, arXiv, November 2025), privacy attacks between agents worked in up to 88% of cases.
- They can piece small answers together into something private that nobody asked for directly.

Free text makes all three easier, because it gives a place to hide details and instructions.

## Decision

Agents never send each other free text. An agent calls Steward's ask tool with a request type from a fixed list. The other agent answers in that type's fixed shape. Code checks both sides.

A message moves in seven steps:

1. Ask: the work agent calls the ask tool with a request type and its fields.
2. Check the request: the type is on the list, the agent may ask this contact, the fields are valid, and the agent is within its daily limit.
3. Forward: the personal agent receives only the typed request.
4. Reply: the personal agent answers in the type's shape.
5. Check the reply: keep the allowed fields, widen busy times, refuse anything on the never-share list, hold anything that needs the user.
6. Deliver: the work agent receives the trimmed reply.
7. Record: the full reply and what crossed go into the flight log (ADR 0004).

The checks:

- Requests are validated with Pydantic v2 in strict mode with extra fields forbidden. An unknown field rejects the request.
- Replies are cut down to the type's allowed fields. Anything extra, such as a note, is dropped and logged.
- Busy times are widened to the contact's detail level. For the user's own agents that is 30-minute blocks, and a block is never cut short.
- Anything on the never-share list is refused: passwords, one-time codes, card and account numbers, ID numbers, health details, and the reason behind any busy time.
- Each agent has a daily limit per contact (10 in the demo). Refused and held requests count towards it.

Anything that fits no type is refused. The asking agent can send it to the user as an approval card instead.

I am going to start with only one request type, then expand to multiple request types later.

v1 starts with a single type, Availability. It carries the core demo (the Manchester trip: share the constraint, not the reason) and it exercises every step of the door end to end. The other types are added one at a time later, each with its own schema and tests.

| Type | Answer shape | Added |
| --- | --- | --- |
| Availability | Time blocks: date, start, end, busy or free, can move or not | Phase 1 |
| Travel limits | A choice from a fixed list, for example "no departures before 9am on weekdays" | Later, after availability works end to end |
| Dietary and access needs | A choice from a fixed list, for example "vegetarian", "step-free access" | Later, after availability works end to end |
| Change notice | Time blocks, sent when something another agent planned around changes | Later, after availability works end to end |
| Address stand-in | [ADDR-1], valid for one approved booking | Later, after availability works end to end |

## Expected result (assumption)

This is how I assume the result will look once the protocol is implemented. I will confirm it in Phase 1. The example is the Manchester trip: the work agent plans a client visit and asks the personal agent about 12 to 15 October.

The request the work agent sends:

```json
{"type": "availability", "contact": "personal", "start_date": "2026-10-12", "end_date": "2026-10-15"}
```

The request holds a type, a contact and a date range. An extra field would reject it.

The personal agent's full reply, which includes two things that must not cross:

```json
{
  "type": "availability",
  "blocks": [
    {
      "date": "2026-10-14",
      "start": "14:15",
      "end": "15:45",
      "status": "busy",
      "can_move": false,
      "title": "Cardiology follow-up with Dr Shah"
    }
  ],
  "note": "hospital appointment, cannot be rescheduled"
}
```

The block fits the Availability shape, but the title and the note are extra, and the times are exact.

What Steward lets through:

```json
{"type": "availability", "blocks": [{"date": "2026-10-14", "start": "14:00", "end": "16:00", "status": "busy", "can_move": false}]}
```

The work agent sees this as "Wed 14 Oct, 2pm to 4pm, busy, can't move".

- The title and the note are dropped, because they are not allowed fields.
- 14:15 to 15:45 is widened to 14:00 to 16:00 (30-minute blocks, never cut short).
- The request counts as question 1 of 10 for the day.

The user's own log gets the full reply, the trimmed reply and the rules that fired (two fields dropped, busy time widened, question counted). The user can see what was shared and what was held back.

A hidden line in a client email makes the work agent ask "why is each day blocked?". There is no request type for reasons, so Steward refuses the ask. It still counts towards the daily limit, and the refusal goes into the log.

## Consequences

- No free text, so there is no place to hide a detail or an instruction.
- Easy to test. A property test can check that no planted detail ever crosses.
- The list is narrow, and v1 has one type, so more asks come to the user as approval cards at first (see ADR 0001). Until a type exists, asks of that kind are refused or sent to the user.
- Code checks the shape of a reply, not whether it is true. If the personal agent marks a movable event as "can't move", that is what is shared.
- Many small valid answers can still add up. The daily limit slows this down but does not prevent it.

## Alternatives considered

Free text plus a filter or an AI check. It can be talked round, and there is no fixed shape to check against.

The Firewalls paper's approach (Abdelnabi et al., arXiv, June 2026). Two firewalls with rules learned automatically from examples cut ConVerse privacy attack success from 84% to 10% for GPT-5. Steward uses the same two ideas, a fixed protocol and task-level detail. The agents speak the protocol directly, and the rules are set by the user and checked by plain code. The paper's 10% is the bar to beat in the Phase 4 comparison.

All five types at once. More surface to get wrong before the pipeline is proven end to end.
