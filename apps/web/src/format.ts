import type { LogEvent } from './api'

type Payload = Record<string, unknown>

function str(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

export function time(ts: string): string {
  return new Date(ts).toLocaleTimeString('en-GB', { hour12: false })
}

export function title(agent: string): string {
  return `${agent.charAt(0).toUpperCase()}${agent.slice(1)} agent`
}

export function usd(value: string): string {
  const amount = Number(value)
  return amount < 0.01 && amount > 0 ? `$${amount.toFixed(4)}` : `$${amount.toFixed(2)}`
}

// One line per event, saying what happened in plain words.
export function summary(event: LogEvent): string {
  const p = event.payload as Payload
  switch (event.kind) {
    case 'model.call': {
      const model = str(p.model).split('/').pop()
      const usage = p.usage as Payload | null
      if (!usage) return `${model}: HTTP ${str(p.status)}, no usage reported`
      const calls = (p.tool_calls as string[] | undefined) ?? []
      const tokens = `${str(usage.prompt_tokens)} in / ${str(usage.completion_tokens)} out`
      const asked = calls.length ? `, asked for ${calls.join(', ')}` : ''
      return `${model}: ${tokens}, $${str(p.cost_usd)}${asked}`
    }
    case 'tool.call':
      return `${str(p.tool)}(${str(p.arguments)}) -> ${str(p.result_bytes)} bytes`
    case 'tool.denied':
      return `${str(p.tool)} denied: ${str(p.reason)}`
    case 'tool.pin_mismatch':
      return `${str(p.tool_id)} changed since it was pinned; disabled`
    case 'tool.pinned':
      return `${str(p.tool_id)} pinned`
    case 'a2a.request': {
      const fields = (p.fields ?? {}) as Payload
      return `asks ${str(p.contact)} for ${str(p.request_type)} ${str(fields.start_date)} to ${str(fields.end_date)} (ask ${str(p.asks_today)} of ${str(p.daily_limit)} today)`
    }
    case 'a2a.reply': {
      const dropped = (p.dropped_fields as string[] | undefined) ?? []
      const rules = (p.rules_fired as string[] | undefined) ?? []
      const out = ((p.reply_out as Payload | undefined)?.blocks as unknown[] | undefined) ?? []
      return `reply: ${out.length} busy blocks crossed; dropped ${dropped.join(', ') || 'nothing'}; rules ${rules.join(', ')}`
    }
    case 'a2a.refused':
    case 'a2a.held':
      return `${str(p.request_type)} to ${str(p.contact)}: ${str(p.reason)}`
    case 'a2a.delivery':
      return `delivery ${p.ok ? 'ok' : 'failed'} in ${Math.round(Number(p.duration_ms) / 1000)} s; tools ${str(p.tools)}`
    case 'auth.denied':
      return `${str(p.path)}: ${str(p.status)} ${str(p.reason)}`
    default:
      return str(p).slice(0, 160)
  }
}

export function tone(event: LogEvent): string {
  if (['a2a.refused', 'tool.denied', 'tool.pin_mismatch', 'auth.denied', 'a2a.timeout'].includes(event.kind))
    return 'text-rose-700 dark:text-rose-300'
  if (event.kind.startsWith('a2a.')) return 'text-indigo-700 dark:text-indigo-300'
  if (event.door === 'model') return 'text-stone-600 dark:text-stone-300'
  if (event.door === 'tool') return 'text-teal-700 dark:text-teal-300'
  return 'text-stone-500 dark:text-stone-400'
}
