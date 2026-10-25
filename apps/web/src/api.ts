// Typed client for Steward's admin API. The types mirror the JSON that
// apps/gateway/src/steward_gateway/admin.py returns.

export type LogEvent = {
  seq: number
  ts: string
  session_id: string
  agent: string | null
  door: 'model' | 'tool' | 'a2a' | 'auth' | 'admin' | 'system'
  kind: string
  payload: Record<string, unknown>
  prev_hash: string
  hash: string
}

export type AgentSummary = {
  id: string
  model: string
  pass: 'active' | 'suspended'
  tools: number
  spend_today_usd: string
  budget_usd_per_day: string
  asks_today: Record<string, number>
}

export type AgentTool = {
  name: string
  tool_id: string
  state: 'pinned' | 'mismatch' | 'missing' | 'built-in'
  fingerprint?: string | null
  pinned?: string | null
}

export type AgentRoute = {
  to: string
  request_types: string[]
  detail_minutes: number
  daily_limit: number
}

export type AgentDetail = AgentSummary & {
  tool_prefix: string
  tool_list: AgentTool[]
  routes: AgentRoute[]
  recent: LogEvent[]
}

export type Status = {
  session_id: string
  head: { seq: number; hash: string }
  checker: string
  demo_anchor: string
  agents: string[]
  pending: number
}

export type VerifyResult = {
  ok: boolean
  rows: number
  head_hash: string
  first_bad_seq: number | null
  reason: string | null
}

export class Unauthorized extends Error {}

export function isAuthError(error: unknown): boolean {
  return error instanceof Unauthorized
}

const TOKEN_KEY = 'steward-owner-token'

export function savedToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function saveToken(token: string | null): void {
  if (token) sessionStorage.setItem(TOKEN_KEY, token)
  else sessionStorage.removeItem(TOKEN_KEY)
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${savedToken() ?? ''}` },
  })
  if (response.status === 401) throw new Unauthorized('owner token required')
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  return (await response.json()) as T
}

export const api = {
  status: () => call<Status>('/admin/status'),
  agents: () => call<AgentSummary[]>('/admin/agents'),
  agent: (id: string) => call<AgentDetail>(`/admin/agents/${encodeURIComponent(id)}`),
  suspend: (id: string) =>
    call<AgentSummary>(`/admin/agents/${encodeURIComponent(id)}/suspend`, { method: 'POST' }),
  resume: (id: string) =>
    call<AgentSummary>(`/admin/agents/${encodeURIComponent(id)}/resume`, { method: 'POST' }),
  log: (params: Record<string, string | number | undefined>) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') query.set(key, String(value))
    }
    return call<LogEvent[]>(`/admin/log?${query}`)
  },
  verify: () => call<VerifyResult>('/admin/log/verify'),
  repin: (toolId: string) =>
    call<{ state: string }>(`/admin/tools/${encodeURIComponent(toolId)}/repin`, {
      method: 'POST',
    }),
  exportLog: async (): Promise<Blob> => {
    const response = await fetch('/admin/log/export.jsonl', {
      headers: { Authorization: `Bearer ${savedToken() ?? ''}` },
    })
    if (response.status === 401) throw new Unauthorized('owner token required')
    return response.blob()
  },
}

// Follows the live log over server-sent events. EventSource cannot send an
// Authorization header, so this reads the stream with fetch instead.
export async function followLog(
  afterSeq: number,
  onEvent: (event: LogEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`/admin/log/stream?after_seq=${afterSeq}`, {
    headers: { Authorization: `Bearer ${savedToken() ?? ''}` },
    signal,
  })
  if (response.status === 401) throw new Unauthorized('owner token required')
  if (!response.ok || !response.body) throw new Error(`stream failed: ${response.status}`)
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    buffer += value.replaceAll('\r\n', '\n')
    let end = buffer.indexOf('\n\n')
    while (end >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (data) onEvent(JSON.parse(data) as LogEvent)
      end = buffer.indexOf('\n\n')
    }
  }
}
