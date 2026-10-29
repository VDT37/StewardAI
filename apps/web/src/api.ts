// Typed client for Steward's admin API. The types mirror the JSON that
// apps/gateway/src/steward_gateway/admin.py returns. In the public demo the same calls go to one
// recording's replayed Steward at /r/<name> (read-only), and /demo/* holds the demo's own routes.

export type Door = 'model' | 'tool' | 'a2a' | 'auth' | 'admin' | 'system'

export type LogEvent = {
  seq: number
  ts: string
  session_id: string
  agent: string | null
  door: Door
  kind: string
  payload: Record<string, unknown>
  prev_hash: string
  hash: string
}

export type Flags = {
  private_read: string[]
  untrusted_seen: string[]
  since: string | null
}

export type AgentSummary = {
  id: string
  model: string
  pass: 'active' | 'suspended' | 'revoked'
  tools_off: string[]
  tools: number
  spend_today_usd: string
  budget_usd_per_day: string
  asks_today: Record<string, number>
  flags: Flags
  holds_pending: number
  trip_limit_gbp: string | null
}

export type AgentTool = {
  name: string
  tool_id: string
  state: 'pinned' | 'mismatch' | 'missing' | 'built-in' | 'off'
  fingerprint?: string | null
  pinned?: string | null
  tool_class?: string | null
  egress?: boolean | null
}

export type AgentRoute = {
  to: string
  request_types: string[]
  detail_minutes: number
  daily_limit: number
  burst_max: number | null
  burst_minutes: number
}

export type AgentDetail = AgentSummary & {
  tool_prefix: string
  tool_list: AgentTool[]
  routes: AgentRoute[]
  recent: LogEvent[]
}

export type ClockInfo = { mode: 'system' | 'story'; rate: number; now: string; london: string }

export type ScenarioState = {
  state: string
  name?: string
  session_id?: string
  beats?: Record<string, unknown>[]
  error?: string
  pending_holds?: string[]
}

export type Status = {
  session_id: string
  session_label: string | null
  head: { seq: number; hash: string }
  checker: string
  demo_anchor: string
  agents: string[]
  pending: number
  holds_pending: number
  clock: ClockInfo
  web_backend: string
  telegram: boolean
  scenario: ScenarioState | null
  mode: 'owner' | 'demo'
  models: Record<string, string | null>
  brief_at: string | null
}

export type Choice = { id: string; label: string }

export type Hold = {
  hold_id: string
  session_id: string
  agent: string
  kind: 'tool' | 'ask' | 'address' | 'pause'
  tool: string
  tool_id: string | null
  args: Record<string, unknown>
  fingerprint: string
  fingerprint_short: string
  rule: string
  rules: string[]
  reason: string
  details: Record<string, unknown>
  choices: Choice[]
  state: string
  created: string
  expires: string
  choice: string | null
  result: Record<string, unknown> | null
  decided: string | null
  tool_class: string | null
  // What started the agent's run, from Steward's records (waiting holds only).
  prompted_by?: string | null
}

export type Exchange = {
  id: string
  ts: string
  asker: string | null
  contact: string | null
  request_type: string
  status: string
  fields?: Record<string, unknown>
  rule?: string
  reason?: string
  hold_id?: string | null
  asks_today?: number
  reply_in?: Record<string, unknown>
  reply_out?: Record<string, unknown>
  dropped_fields?: string[]
  rules_fired?: string[]
  detail_minutes?: number
  invalid_replies?: { fields: unknown; reason: string }[]
  notices?: Record<string, unknown>[]
  about?: string[]
  stand_in?: { token: string; trip_date: string; expires: string }
}

export type VerifyResult = {
  ok: boolean
  rows: number
  head_hash: string
  first_bad_seq: number | null
  reason: string | null
}

export type ReplayResult = {
  ok?: boolean
  state?: string
  error?: string
  session_id?: string
  compared?: number
  recorded_decisions?: number
  replayed_decisions?: number
  events_in_recording?: number
  seconds?: number
  first_difference?: {
    index: number
    recorded: { kind: string; payload: unknown } | null
    replayed: { kind: string; payload: unknown } | null
  } | null
}

export type PolicyView = {
  agents: Record<
    string,
    {
      model: string
      tools: string[]
      budget_usd_per_day: string
      trip_limit_gbp: string | null
      delivery: { kind: string } | null
    }
  >
  contacts: {
    from: string
    to: string
    request_types: string[]
    detail_minutes: number
    daily_limit: number
    burst_max: number | null
    burst_minutes: number
  }[]
  tools: Record<
    string,
    {
      class: string
      private: boolean
      untrusted: boolean
      trust_field: string | null
      egress: boolean | null
      stand_in_args: string[]
      versioned: unknown
    }
  >
  trusted_mail_domains: string[]
  never_share: string[]
  holds: { expires_hours: number; link_minutes: number; stand_in_hours: number }
}

export type QuestionHold = {
  hold_id: string
  fingerprint: string
  tool: string
  choices: [string, string][]
  state: string
}

export type Brief = {
  state?: 'none'
  brief_id: string
  ts: string
  session_id: string
  text: string
  summary: string
  source: 'model' | 'template'
  model: string | null
  problems: string[] | null
  facts: Record<string, unknown>
  question: {
    kind: string
    text: string
    approve: string
    deny: string
    holds: QuestionHold[]
  } | null
}

export type MailItem = {
  message_id: string
  subject: string
  sender_domain: string
  signs: string[]
  reasons: string[]
  links: { url: string; text: string; host: string }[]
  hidden_text: string[]
  score: number
  first_seen: string
  agent: string
  reads: number
  verdict: 'phishing' | 'not_phishing' | null
  reported_by_agent: boolean
}

export type WatcherPause = { ts: string; agent: string; rule: string; detail: string; hold_id: string }

export type WatcherState = {
  on: boolean
  limits: Record<string, number> | null
  agents: Record<string, { pass: 'active' | 'paused' | 'revoked'; tools_off: string[] }>
  pauses: WatcherPause[]
  baselines: { agent: string; metric: string; value: number; median: number | null; verdict: string }[]
}

export type DemoRecording = {
  name: string
  label: string
  kind: 'live' | 'scripted'
  session_id: string
  recorded_on: string | null
  events: number
  models_used: Record<string, string[]>
  models_set: Record<string, string | null>
  brief: { source: string; model: string | null; problems: string[] | null } | null
  beats: { ts: string; beat: string; actor: string }[]
  replay: ReplayResult | null
  rerun: ReplayResult | null
}

export type ReplyExample = {
  request_type: string
  request: Record<string, unknown>
  reply: unknown
  recording: string
}

export type DemoInfo = {
  mode: 'demo'
  default: string | null
  recordings: DemoRecording[]
  examples: {
    reply: ReplyExample[]
    ask: { request_type: string; fields: unknown }[]
    mail: { from: string; subject: string; body: string; body_html: string } | null
    lock: {
      hold: { agent: string; tool: string; args: Record<string, unknown>; hold_id: string }
      fingerprint: string
      recording: string
    } | null
  }
  limits: { body_bytes: number; posts_per_minute: number; replay_cache_s: number }
}

export type CheckResult = Record<string, unknown> & { kind: string }

export class Unauthorized extends Error {}

export function isAuthError(error: unknown): boolean {
  return error instanceof Unauthorized
}

const TOKEN_KEY = 'steward-owner-token'

// '' for the owner's Steward; '/r/<recording>' for one recording in the public demo.
let base = ''

export function setBase(prefix: string): void {
  base = prefix
}

export function savedToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function saveToken(token: string | null): void {
  if (token) sessionStorage.setItem(TOKEN_KEY, token)
  else sessionStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path.startsWith('/admin') ? base + path : path, {
    ...init,
    headers: {
      ...init.headers,
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      Authorization: `Bearer ${savedToken() ?? ''}`,
    },
  })
  if (response.status === 401) throw new Unauthorized('owner token required')
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

const post = <T>(path: string, body?: unknown) =>
  call<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

const enc = encodeURIComponent

export const api = {
  status: () => call<Status>('/admin/status'),
  agents: () => call<AgentSummary[]>('/admin/agents'),
  agent: (id: string) => call<AgentDetail>(`/admin/agents/${enc(id)}`),
  suspend: (id: string) => post<AgentSummary>(`/admin/agents/${enc(id)}/suspend`),
  resume: (id: string) => post<AgentSummary>(`/admin/agents/${enc(id)}/resume`),
  log: (params: Record<string, string | number | undefined>) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') query.set(key, String(value))
    }
    return call<LogEvent[]>(`/admin/log?${query}`)
  },
  verify: () => call<VerifyResult>('/admin/log/verify'),
  repin: (toolId: string) => post<{ state: string }>(`/admin/tools/${enc(toolId)}/repin`),
  holds: (state = 'pending') => call<Hold[]>(`/admin/holds?state=${enc(state)}`),
  decide: (hold: Hold, choice: string) =>
    post<Hold>(`/admin/holds/${enc(hold.hold_id)}/decide`, {
      choice,
      fingerprint: hold.fingerprint,
    }),
  exchanges: () => call<Exchange[]>('/admin/exchanges'),
  clearFlags: (agent: string) => post<unknown>(`/admin/flags/${enc(agent)}/clear`),
  policy: () => call<PolicyView>('/admin/policy'),
  replay: (sessionId?: string) => post<ReplayResult>('/admin/replay', { session_id: sessionId ?? null }),
  lastReplay: () => call<ReplayResult>('/admin/replay'),
  scenario: () => call<ScenarioState>('/admin/scenario'),
  clock: () => call<ClockInfo>('/admin/clock'),
  resetClock: () => call<ClockInfo>('/admin/clock', { method: 'DELETE' }),
  runScenario: (approvals: 'scripted' | 'manual') =>
    post<ScenarioState>('/admin/scenario/run', { name: 'manchester', approvals, speed: 60 }),
  revoke: (id: string) => post<AgentSummary>(`/admin/agents/${enc(id)}/revoke`),
  tool: (id: string, tool: string, on: boolean) =>
    post<AgentSummary>(`/admin/agents/${enc(id)}/tools/${enc(tool)}/${on ? 'on' : 'off'}`),
  brief: () => call<Brief>('/admin/brief'),
  runBrief: () => post<Brief>('/admin/brief/run'),
  answerBrief: (briefId: string, choice: 'approve' | 'deny') =>
    post<{ outcomes: { hold_id: string; state?: string; error?: string }[] }>('/admin/brief/answer', {
      brief_id: briefId,
      choice,
    }),
  mail: () => call<MailItem[]>('/admin/mail'),
  mailVerdict: (messageId: string, verdict: 'phishing' | 'not_phishing') =>
    post<unknown>(`/admin/mail/${enc(messageId)}/verdict`, { verdict }),
  watcher: () => call<WatcherState>('/admin/watcher'),
  demoInfo: () => call<DemoInfo>('/demo/info'),
  demoReplay: (recording: string) =>
    post<ReplayResult & { cached?: boolean; ran_at?: string }>('/demo/replay', { recording }),
  demoCheck: (body: Record<string, unknown>) => post<CheckResult>('/demo/check', body),
  exportLog: async (): Promise<Blob> => {
    const response = await fetch(`${base}/admin/log/export.jsonl`, {
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
  const response = await fetch(`${base}/admin/log/stream?after_seq=${afterSeq}`, {
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
