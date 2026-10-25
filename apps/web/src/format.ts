// Plain-English wording for everything the console shows. The log stores codes
// (a2a.reply, trifecta); the owner reads sentences.
import type { LogEvent } from './api'

type Payload = Record<string, unknown>

export type Outcome = 'allowed' | 'held' | 'refused' | 'trimmed' | 'owner' | 'note'

const LONDON = 'Europe/London'

function str(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

export function who(agent: string | null | undefined): string {
  if (!agent) return 'Steward'
  if (agent === 'owner') return 'You'
  return `${agent.charAt(0).toUpperCase()}${agent.slice(1)} agent`
}

// "Sun 19:05:12", in London time (story time while a scenario runs).
export function when(ts: string): string {
  const date = new Date(ts)
  const day = date.toLocaleDateString('en-GB', { weekday: 'short', timeZone: LONDON })
  const time = date.toLocaleTimeString('en-GB', { hour12: false, timeZone: LONDON })
  return `${day} ${time}`
}

// "Thu 15 Oct" from an ISO date or timestamp.
export function day(value: string): string {
  const date = value.length === 10 ? new Date(`${value}T12:00:00Z`) : new Date(value)
  return date.toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: LONDON,
  })
}

export function usd(value: string): string {
  const amount = Number(value)
  return amount < 0.01 && amount > 0 ? `$${amount.toFixed(4)}` : `$${amount.toFixed(2)}`
}

export function shortHash(hash: string): string {
  return `${hash.slice(0, 4)}...${hash.slice(-3)}`
}

function trainTime(optionId: string): string {
  const match = optionId.match(/(\d{2})(\d{2})$/)
  if (!match) return optionId
  const time = `${match[1]}:${match[2]}`
  return optionId.includes('return') ? `the ${time} return` : `the ${time} train`
}

function span(fields: Payload): string {
  const start = str(fields.start_date)
  const end = str(fields.end_date)
  if (!start) return ''
  return start === end ? day(start) : `${day(start)} to ${day(end)}`
}

// What an agent's tool call does, as a verb phrase: "book the 09:30 train".
export function callPhrase(tool: string, args: Payload = {}): string {
  switch (tool) {
    case 'calendar_list_events':
      return `read its calendar, ${span(args)}`
    case 'mail_list_messages':
    case 'gmail_list_messages':
      return 'list its mail'
    case 'mail_read_message':
    case 'gmail_read_message':
      return `read the email ${str(args.id)}`
    case 'mail_send_message':
      return `email ${str(args.to)}: "${str(args.subject)}"`
    case 'todo_list_items':
      return 'read the shared to-do list'
    case 'todo_update_item':
      return `update the to-do item ${str(args.id)} (it read version ${str(args.expected_version)})`
    case 'todo_delete_item':
      return `delete the to-do item ${str(args.id)}`
    case 'travel_search_trains':
      return `search trains for ${day(str(args.date))}`
    case 'travel_list_bookings':
      return 'list its bookings'
    case 'travel_book_train':
      return `book ${trainTime(str(args.option_id))}`
    case 'travel_book_taxi':
      return `book a taxi for ${day(str(args.date))}, pickup ${str(args.pickup_time)}, from ${str(args.address)}`
    case 'travel_change_booking':
      return args.option_id
        ? `move booking ${str(args.booking_id)} to ${trainTime(str(args.option_id))}`
        : `move the pickup for booking ${str(args.booking_id)} to ${str(args.pickup_time)}`
    case 'web_search':
      return `search the web for "${str(args.query)}"`
    case 'web_extract': {
      const url = str(args.url)
      try {
        const parsed = new URL(url)
        return `read the web page ${parsed.host}${parsed.pathname}`
      } catch {
        return `read the web page ${url}`
      }
    }
    case 'profile_read_needs':
      return 'read the needs profile'
    case 'hold_status':
      return `check held call ${str(args.hold_id)}`
    default:
      return `use ${tool}`
  }
}

// What an ask is for: "availability, Mon 12 Oct to Thu 15 Oct".
export function askPhrase(requestType: string, fields: unknown): string {
  const f = (fields && typeof fields === 'object' ? fields : {}) as Payload
  switch (requestType) {
    case 'availability':
      return `availability, ${span(f)}`
    case 'travel_limits':
      return 'travel limits'
    case 'needs':
      return 'dietary and access needs'
    case 'address_for_booking':
      return `your home address, for a taxi on ${day(str(f.trip_date))}`
    case 'change_notice':
      return 'a change notice'
    default:
      return `"${requestType}"`
  }
}

const RULES: Record<string, string> = {
  trifecta: 'Private data and untrusted content seen',
  send_needs_owner: 'Sending always needs you',
  paid_change_needs_owner: 'Paid changes need you',
  destructive_needs_owner: 'Deleting needs you',
  trip_limit: 'Over the trip limit',
  stale_write: 'Changed since it was read',
  address_needs_owner: 'Addresses always need you',
  daily_limit: 'Daily limit reached',
  burst: 'Too many asks at once',
  unknown_request_type: 'Not a question it may ask',
  invalid_fields: 'Fields not allowed',
  no_route: 'No route to that agent',
  outside_window: 'Dates out of range',
  stand_in_misuse: 'Stand-in used in the wrong place',
  stand_in_unknown: 'Unknown stand-in',
  stand_in_expired: 'Stand-in expired',
  stand_in_used: 'Stand-in already used',
  stand_in_required: 'Needs an address stand-in',
  stand_in_covered: 'Covered by your address approval',
  never_share_free_text: 'Free text blocked',
  invalid_reply: 'Reply did not fit the type',
  invalid_call: 'Call could not be checked',
  checker_error: 'Checker failed, held',
  request_ok: 'Allowed',
  allowed: 'Allowed',
}

export function ruleLabel(rule: string | undefined | null): string {
  if (!rule) return ''
  return RULES[rule] ?? rule.replaceAll('_', ' ')
}

const CHANGES: Record<string, string> = {
  'chg-user-asks-for-trip': 'You asked the work agent to plan the Manchester visit',
  'chg-poisoned-email-arrives': 'A lookalike client email arrived in the work mailbox',
  'chg-blood-test-added': 'A blood test appeared on your personal calendar',
}

function listCount(value: unknown): number {
  if (!value || typeof value !== 'object') return 0
  const v = value as Payload
  for (const key of ['blocks', 'limits']) {
    if (Array.isArray(v[key])) return (v[key] as unknown[]).length
  }
  const dietary = Array.isArray(v.dietary) ? v.dietary.length : 0
  const access = Array.isArray(v.access) ? v.access.length : 0
  return dietary + access
}

export type Line = { actor: string; text: string; outcome: Outcome; rule?: string }

// One sentence per event, with who did it and how it ended.
export function describe(event: LogEvent): Line {
  const p = event.payload as Payload
  const call = (p.call ?? {}) as Payload
  const args = (call.arguments ?? {}) as Payload
  const actor = who(event.agent)
  const rule = typeof p.rule === 'string' ? p.rule : undefined
  switch (event.kind) {
    case 'tool.call':
      return {
        actor,
        text: `Will ${callPhrase(str(call.tool), args)}`,
        outcome: 'allowed',
        rule: rule === 'stand_in_covered' ? rule : undefined,
      }
    case 'tool.result': {
      const flags = (p.flags_set as string[] | undefined) ?? []
      const flagText = flags
        .map((f) => (f === 'private_read' ? 'has now read private data' : 'has now seen untrusted content'))
        .join(' and ')
      return {
        actor,
        text: flags.length ? `Got its result; it ${flagText}` : `Got its result (${p.is_error ? 'an error' : 'ok'})`,
        outcome: 'note',
      }
    }
    case 'tool.held':
      return { actor, text: `Wanted to ${callPhrase(str(call.tool), args)}`, outcome: 'held', rule }
    case 'tool.refused':
      return { actor, text: `Tried to ${callPhrase(str(call.tool), args)}`, outcome: 'refused', rule }
    case 'tool.denied':
      return { actor, text: `Tried ${str(call.tool) || str(p.tool)}: ${str(p.reason)}`, outcome: 'refused' }
    case 'hold.status':
      return { actor, text: `Checked held call ${str(p.hold_id)}: ${str(p.status)}`, outcome: 'note' }
    case 'a2a.request':
      return {
        actor,
        text: `Asked the ${str(p.contact)} agent for ${askPhrase(str(p.request_type), p.fields)} (ask ${str(p.asks_today)} of ${str(p.daily_limit)} today)`,
        outcome: 'allowed',
      }
    case 'a2a.refused':
      return { actor, text: `Asked for ${askPhrase(str(p.request_type), p.fields)}`, outcome: 'refused', rule }
    case 'a2a.held':
      return {
        actor,
        text: p.request_type
          ? `Asked for ${askPhrase(str(p.request_type), p.fields)}`
          : 'Sent a reply Steward could not check',
        outcome: 'held',
        rule,
      }
    case 'a2a.reply': {
      const dropped = (p.dropped_fields as string[] | undefined) ?? []
      const rules = (p.rules_fired as string[] | undefined) ?? []
      const crossed = listCount(p.reply_out)
      const trimmed = dropped.length > 0 || rules.length > 0
      const extra = dropped.length ? `; Steward dropped ${dropped.join(', ')}` : ''
      return {
        actor,
        text: `Answered; ${crossed} item${crossed === 1 ? '' : 's'} crossed${extra}`,
        outcome: trimmed ? 'trimmed' : 'allowed',
      }
    }
    case 'a2a.reply_invalid':
      return { actor, text: `Sent a reply that did not fit: ${str(p.reason)}`, outcome: 'refused' }
    case 'a2a.reply_rejected':
      return { actor, text: 'Replied to a request that was not open for it', outcome: 'refused' }
    case 'a2a.delivery':
      return {
        actor,
        text: `Finished its run in ${Math.round(Number(p.duration_ms) / 1000)} s${p.ok ? '' : ' (with an error)'}`,
        outcome: 'note',
      }
    case 'a2a.timeout':
    case 'a2a.no_reply':
      return { actor, text: 'No answer came back, so nothing was shared', outcome: 'note' }
    case 'a2a.notice': {
      const notices = (p.notices as Payload[] | undefined) ?? []
      const first = notices[0] ?? {}
      const move = first.can_move ? 'can move' : "can't move"
      const change = first.change === 'now_busy' ? 'now busy' : 'now free'
      return {
        actor: 'Steward',
        text: `Told the ${str(p.asker)} agent: ${day(str(first.date))}, ${str(first.start)} to ${str(first.end)}, ${change}, ${move}`,
        outcome: 'note',
      }
    }
    case 'a2a.notice_delivery':
    case 'hold.told':
      return { actor, text: `Received a note from Steward (run ${p.ok ? 'finished' : 'failed'})`, outcome: 'note' }
    case 'stand_in.issued':
      return {
        actor: 'Steward',
        text: `Issued ${str(p.token)} for one taxi booking on ${day(str(p.trip_date))}`,
        outcome: 'note',
      }
    case 'hold.approved': {
      const action = (p.owner_action ?? {}) as Payload
      return { actor: 'You', text: `Approved (${str(action.choice).replace('_', ' ')}) held call ${str(p.hold_id)}`, outcome: 'owner' }
    }
    case 'hold.denied':
      return { actor: 'You', text: `Said no to held call ${str(p.hold_id)}`, outcome: 'owner' }
    case 'hold.rejected':
      return { actor: 'Steward', text: 'An approval did not match the held call, so nothing ran', outcome: 'refused' }
    case 'hold.expired':
      return { actor: 'Steward', text: `Held call ${str(p.hold_id)} expired; nothing ran`, outcome: 'note' }
    case 'intent.executed':
      return {
        actor: 'Steward',
        text: `Ran the approved call ${str(p.tool)} once: ${str(p.state)}`,
        outcome: p.state === 'done' ? 'allowed' : 'refused',
      }
    case 'intent.duplicate':
      return { actor: 'Steward', text: 'Stopped a second run of the same approval', outcome: 'note' }
    case 'model.call': {
      const usage = p.usage as Payload | null
      const model = str(p.model).split('/').pop()
      if (!usage) return { actor, text: `${model}: HTTP ${str(p.status)}, no usage`, outcome: 'note' }
      return {
        actor,
        text: `Model call to ${model}: ${str(usage.prompt_tokens)} tokens in, ${str(usage.completion_tokens)} out, $${str(p.cost_usd)}`,
        outcome: 'note',
      }
    }
    case 'model.budget_refused':
      return { actor, text: `Model call refused: spent $${str(p.spent_usd)} of $${str(p.budget_usd)} today`, outcome: 'refused' }
    case 'flags.cleared':
      return { actor, text: `Trifecta flags cleared (${str(p.reason)})`, outcome: 'note' }
    case 'world.change':
      return { actor: 'Story', text: CHANGES[str(p.id)] ?? str(p.id), outcome: 'note' }
    case 'world.reset':
      return { actor: 'Story', text: 'The demo world was reset', outcome: 'note' }
    case 'scenario.beat':
      return { actor: 'Story', text: `${str(p.beat)}${p.actor === 'scripted' ? ' (scripted)' : ''}`, outcome: 'note' }
    case 'scenario.start':
      return { actor: 'Story', text: 'The Manchester evening started', outcome: 'note' }
    case 'scenario.end':
      return { actor: 'Story', text: 'The Manchester evening finished', outcome: 'note' }
    case 'scenario.failed':
      return { actor: 'Story', text: `The scenario stopped: ${str(p.error)}`, outcome: 'refused' }
    case 'auth.denied':
      return { actor, text: `A request to ${str(p.path)} was turned away (${str(p.status)})`, outcome: 'refused' }
    case 'admin.pass_suspended':
      return { actor: 'You', text: `Suspended the ${str(p.agent)} agent's pass`, outcome: 'owner' }
    case 'admin.pass_resumed':
      return { actor: 'You', text: `Resumed the ${str(p.agent)} agent's pass`, outcome: 'owner' }
    case 'tool.pinned':
      return { actor: 'Steward', text: `Pinned the tool ${str(p.tool_id)}`, outcome: 'note' }
    case 'tool.pin_mismatch':
      return { actor: 'Steward', text: `${str(p.tool_id)} changed since it was pinned; switched off`, outcome: 'refused' }
    case 'system.start':
      return { actor: 'Steward', text: 'Steward started', outcome: 'note' }
    case 'system.session':
      return { actor: 'Steward', text: `New session${p.label ? ` (${str(p.label)})` : ''}`, outcome: 'note' }
    case 'system.clock':
      return { actor: 'Steward', text: p.mode === 'story' ? `Story clock: ${when(str(p.now))}` : 'Back to the real clock', outcome: 'note' }
    default:
      return { actor, text: event.kind, outcome: 'note' }
  }
}

// The events worth reading by default; the rest (model calls, results, deliveries) are a
// toggle away.
const QUIET = new Set([
  'model.call',
  'a2a.delivery',
  'a2a.notice_delivery',
  'hold.told',
  'hold.status',
  'tool.pinned',
  'system.clock',
  'system.start',
  'system.stop',
])

export function isDecision(event: LogEvent): boolean {
  if (event.kind === 'tool.result') {
    return ((event.payload.flags_set as string[] | undefined) ?? []).length > 0
  }
  return !QUIET.has(event.kind)
}
