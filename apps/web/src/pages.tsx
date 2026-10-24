import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  api,
  followLog,
  type AgentDetail,
  type AgentSummary,
  type LogEvent,
  type VerifyResult,
} from './api'
import { summary, time, title, tone, usd } from './format'

type OnAuthError = (error: unknown) => void

// Loads once, then every 5 seconds. load must be a stable function (module level or memoised).
function useLoad<T>(load: () => Promise<T>, onError: OnAuthError) {
  const [data, setData] = useState<T | null>(null)
  const reload = useCallback(() => {
    load().then(setData, onError)
  }, [load, onError])
  useEffect(() => {
    reload()
    const timer = setInterval(reload, 5000)
    return () => clearInterval(timer)
  }, [reload])
  return [data, reload] as const
}

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <section
      className={`rounded-lg border border-stone-200 bg-white p-4 dark:border-stone-700 dark:bg-stone-900 ${className}`}
    >
      {children}
    </section>
  )
}

function Badge({ children, kind }: { children: React.ReactNode; kind: 'ok' | 'warn' | 'muted' }) {
  const colours = {
    ok: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-200',
    warn: 'bg-rose-100 text-rose-800 dark:bg-rose-900/50 dark:text-rose-200',
    muted: 'bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300',
  }
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${colours[kind]}`}>{children}</span>
  )
}

function Spend({ spent, budget }: { spent: string; budget: string }) {
  const share = Math.min(100, (Number(spent) / Math.max(Number(budget), 1e-9)) * 100)
  return (
    <div>
      <div className="flex justify-between text-sm">
        <span>Spend today</span>
        <span className="tabular-nums">
          {usd(spent)} of {usd(budget)}
        </span>
      </div>
      <div className="mt-1 h-1.5 rounded bg-stone-200 dark:bg-stone-700">
        <div
          className={`h-1.5 rounded ${share >= 100 ? 'bg-rose-500' : 'bg-emerald-500'}`}
          style={{ width: `${share}%` }}
        />
      </div>
      <p className="mt-1 text-xs text-stone-500">Metered only; budgets block from Phase 2.</p>
    </div>
  )
}

export function EventRow({ event }: { event: LogEvent }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="border-b border-stone-100 py-1.5 last:border-0 dark:border-stone-800">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="grid w-full grid-cols-[3.5rem_4.5rem_5rem_9.5rem_1fr] gap-2 text-left text-sm"
      >
        <span className="tabular-nums text-stone-400">{event.seq}</span>
        <span className="tabular-nums text-stone-500">{time(event.ts)}</span>
        <span className="truncate font-medium">{event.agent ?? '-'}</span>
        <span className={`truncate font-mono text-xs leading-5 ${tone(event)}`}>{event.kind}</span>
        <span className="truncate">{summary(event)}</span>
      </button>
      {open && (
        <pre className="mt-2 max-h-96 overflow-auto rounded bg-stone-50 p-3 text-xs dark:bg-stone-950">
          {JSON.stringify(event.payload, null, 2)}
          {`\n\nhash ${event.hash}\nprev ${event.prev_hash}\nsession ${event.session_id}`}
        </pre>
      )}
    </li>
  )
}

export function AgentsPage({ onError }: { onError: OnAuthError }) {
  const [agents] = useLoad(api.agents, onError)
  if (!agents) return <p className="text-stone-500">Loading…</p>
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {agents.map((agent: AgentSummary) => (
        <a key={agent.id} href={`#/agents/${agent.id}`} className="block">
          <Card className="hover:border-stone-400">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">{title(agent.id)}</h2>
              <Badge kind={agent.pass === 'active' ? 'ok' : 'warn'}>pass {agent.pass}</Badge>
            </div>
            <p className="mt-1 font-mono text-xs text-stone-500">{agent.model}</p>
            <p className="mt-3 text-sm">{agent.tools} tools on its pass</p>
            <p className="text-sm">
              Asks today:{' '}
              {Object.entries(agent.asks_today)
                .map(([to, n]) => `${n} to ${to}`)
                .join(', ') || 'none'}
            </p>
            <div className="mt-3">
              <Spend spent={agent.spend_today_usd} budget={agent.budget_usd_per_day} />
            </div>
          </Card>
        </a>
      ))}
    </div>
  )
}

export function AgentPage({ id, onError }: { id: string; onError: OnAuthError }) {
  const load = useCallback(() => api.agent(id), [id])
  const [agent, reload] = useLoad<AgentDetail>(load, onError)
  if (!agent) return <p className="text-stone-500">Loading…</p>
  const toggle = () =>
    (agent.pass === 'active' ? api.suspend(id) : api.resume(id)).then(reload, onError)
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">{title(agent.id)}</h2>
          <div className="flex items-center gap-2">
            <Badge kind={agent.pass === 'active' ? 'ok' : 'warn'}>pass {agent.pass}</Badge>
            <button
              type="button"
              onClick={toggle}
              className="rounded border border-stone-300 px-2 py-1 text-sm hover:bg-stone-100 dark:border-stone-600 dark:hover:bg-stone-800"
            >
              {agent.pass === 'active' ? 'Suspend pass' : 'Resume pass'}
            </button>
          </div>
        </div>
        <p className="mt-1 font-mono text-xs text-stone-500">{agent.model}</p>
        <div className="mt-3 max-w-md">
          <Spend spent={agent.spend_today_usd} budget={agent.budget_usd_per_day} />
        </div>
      </Card>
      <Card>
        <h3 className="font-semibold">Tools on the pass</h3>
        <table className="mt-2 w-full text-sm">
          <thead className="text-left text-xs text-stone-500">
            <tr>
              <th className="py-1">Agent sees</th>
              <th>Source</th>
              <th>Pin</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {agent.tool_list.map((tool) => (
              <tr key={tool.name} className="border-t border-stone-100 dark:border-stone-800">
                <td className="py-1 font-mono text-xs">
                  {agent.tool_prefix}
                  {tool.name}
                </td>
                <td className="font-mono text-xs text-stone-500">{tool.tool_id}</td>
                <td>
                  <Badge
                    kind={tool.state === 'mismatch' || tool.state === 'missing' ? 'warn' : 'ok'}
                  >
                    {tool.state}
                  </Badge>{' '}
                  {tool.fingerprint && (
                    <span className="font-mono text-xs text-stone-400">
                      {tool.fingerprint.slice(0, 10)}
                    </span>
                  )}
                </td>
                <td className="text-right">
                  {tool.state === 'mismatch' && (
                    <button
                      type="button"
                      onClick={() => api.repin(tool.tool_id).then(reload, onError)}
                      className="rounded border border-rose-300 px-2 py-0.5 text-xs text-rose-700"
                    >
                      Accept change
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {agent.routes.length > 0 && (
        <Card>
          <h3 className="font-semibold">May ask</h3>
          <ul className="mt-2 text-sm">
            {agent.routes.map((route) => (
              <li key={route.to}>
                {route.to}: {route.request_types.join(', ')}; busy times widened to{' '}
                {route.detail_minutes} minutes; {agent.asks_today[route.to] ?? 0} of{' '}
                {route.daily_limit} asks used today
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card>
        <h3 className="font-semibold">Recent events</h3>
        <ul className="mt-2">
          {agent.recent.map((event) => (
            <EventRow key={event.seq} event={event} />
          ))}
        </ul>
      </Card>
    </div>
  )
}

const DOORS = ['', 'model', 'tool', 'a2a', 'auth', 'admin', 'system']

export function LogPage({ onError }: { onError: OnAuthError }) {
  const [events, setEvents] = useState<LogEvent[]>([])
  const [live, setLive] = useState(false)
  const [door, setDoor] = useState('')
  const [agent, setAgent] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    let stopped = false
    api.log({ limit: 300 }).then((initial) => {
      if (stopped) return
      setEvents(initial)
      const after = initial.length ? initial[0].seq : 0
      setLive(true)
      followLog(
        after,
        (event) => setEvents((current) => [event, ...current].slice(0, 2000)),
        controller.signal,
      )
        .catch((error: unknown) => {
          if (!controller.signal.aborted) onError(error)
        })
        .finally(() => setLive(false))
    }, onError)
    return () => {
      stopped = true
      controller.abort()
    }
  }, [onError])

  const agents = useMemo(
    () => [...new Set(events.map((e) => e.agent).filter((a): a is string => !!a))].sort(),
    [events],
  )
  const shown = events.filter((e) => (!door || e.door === door) && (!agent || e.agent === agent))
  const select =
    'rounded border border-stone-300 bg-white px-2 py-1 text-sm dark:border-stone-600 dark:bg-stone-900'
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-semibold">Live log</h2>
        <Badge kind={live ? 'ok' : 'muted'}>{live ? 'live' : 'not connected'}</Badge>
        <select className={select} value={door} onChange={(e) => setDoor(e.target.value)}>
          {DOORS.map((d) => (
            <option key={d} value={d}>
              {d ? `${d} door` : 'all doors'}
            </option>
          ))}
        </select>
        <select className={select} value={agent} onChange={(e) => setAgent(e.target.value)}>
          <option value="">all agents</option>
          {agents.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <span className="text-xs text-stone-500">Newest first. Click a row for the full event.</span>
      </div>
      <ul className="mt-3">
        {shown.map((event) => (
          <EventRow key={event.seq} event={event} />
        ))}
      </ul>
    </Card>
  )
}

export function ChainPage({ onError }: { onError: OnAuthError }) {
  const [result, setResult] = useState<VerifyResult | null>(null)
  const [checking, setChecking] = useState(true)
  const run = useCallback(() => {
    api
      .verify()
      .then(setResult, onError)
      .finally(() => setChecking(false))
  }, [onError])
  useEffect(run, [run])
  const verify = () => {
    setChecking(true)
    run()
  }
  const download = () =>
    api.exportLog().then((blob) => {
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'steward-log.jsonl'
      link.click()
      URL.revokeObjectURL(url)
    }, onError)
  return (
    <Card>
      <h2 className="font-semibold">Chain status</h2>
      <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
        Every event stores the hash of the one before it. Verify recomputes every hash from the
        first row and names the first row that does not match.
      </p>
      {result && (
        <div className="mt-4 space-y-1 text-sm">
          <p>
            <Badge kind={result.ok ? 'ok' : 'warn'}>{result.ok ? 'chain intact' : 'chain broken'}</Badge>{' '}
            {result.rows} rows checked
          </p>
          {!result.ok && (
            <p className="text-rose-700 dark:text-rose-300">
              First bad row: {result.first_bad_seq} ({result.reason})
            </p>
          )}
          <p className="break-all font-mono text-xs text-stone-500">head {result.head_hash}</p>
        </div>
      )}
      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={verify}
          disabled={checking}
          className="rounded bg-stone-900 px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-stone-100 dark:text-stone-900"
        >
          {checking ? 'Checking…' : 'Verify again'}
        </button>
        <button
          type="button"
          onClick={download}
          className="rounded border border-stone-300 px-3 py-1.5 text-sm dark:border-stone-600"
        >
          Export JSONL
        </button>
      </div>
    </Card>
  )
}
