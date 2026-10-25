// Steward console (Phase 2): approvals first, then what the agents did, what crossed between
// them, the agents, the recorder and the policy. Served by Steward at /console and reached
// through an SSH tunnel until Phase 3.
import { useCallback, useEffect, useState } from 'react'
import { api, isAuthError, saveToken, savedToken, type Status } from './api'
import { when } from './format'
import { ActivityPage } from './pages/Activity'
import { AgentPage, AgentsPage } from './pages/Agents'
import { ApprovalsPage } from './pages/Approvals'
import { ExchangesPage } from './pages/Exchanges'
import { PolicyPage } from './pages/Policy'
import { RecorderPage } from './pages/Recorder'

function useHashRoute(): string {
  const [hash, setHash] = useState(window.location.hash || '#/approvals')
  useEffect(() => {
    const update = () => setHash(window.location.hash || '#/approvals')
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  return hash
}

function SignIn({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState('')
  return (
    <main className="mx-auto max-w-md px-4 py-24">
      <h1 className="text-3xl font-bold">Steward</h1>
      <p className="mt-3 text-muted">
        Paste the owner token (STEWARD_OWNER_TOKEN in the VM's .env). It stays in this browser tab
        only.
      </p>
      <form
        className="mt-6 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          saveToken(token.trim())
          onDone()
        }}
      >
        <label className="sr-only" htmlFor="token">
          Owner token
        </label>
        <input
          id="token"
          type="password"
          autoComplete="off"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          className="flex-1 rounded-lg border border-line bg-card px-3 py-2"
          placeholder="Owner token"
        />
        <button type="submit" className="rounded-lg bg-ink px-4 py-2 font-bold text-card">
          Sign in
        </button>
      </form>
    </main>
  )
}

const NAV = [
  ['#/approvals', 'Waiting for you'],
  ['#/activity', 'Activity'],
  ['#/shared', 'Shared and held'],
  ['#/agents', 'Agents'],
  ['#/recorder', 'Recorder'],
  ['#/policy', 'Policy'],
] as const

function Clock({ status }: { status: Status }) {
  const story = status.clock.mode === 'story'
  return (
    <div className={`rounded-lg px-3 py-2 ${story ? 'bg-work-soft text-work' : 'text-muted'}`}>
      <p className="font-bold">{story ? 'Story time' : 'Real time'}</p>
      <p className="tabular-nums">{when(status.clock.now)}</p>
    </div>
  )
}

function App() {
  const [signedIn, setSignedIn] = useState(() => !!savedToken())
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)
  const hash = useHashRoute()

  const onError = useCallback((reason: unknown) => {
    if (isAuthError(reason)) {
      saveToken(null)
      setSignedIn(false)
    } else {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }, [])

  useEffect(() => {
    if (!signedIn) return
    const load = () =>
      api.status().then((value) => {
        setStatus(value)
        setError(null)
      }, onError)
    load()
    const timer = setInterval(load, 2500)
    return () => clearInterval(timer)
  }, [signedIn, onError])

  if (!signedIn) return <SignIn onDone={() => setSignedIn(true)} />

  const agentMatch = hash.match(/^#\/agents\/([\w-]+)/)
  let page
  if (agentMatch) page = <AgentPage key={agentMatch[1]} id={agentMatch[1]} onError={onError} />
  else if (hash.startsWith('#/activity')) page = <ActivityPage onError={onError} />
  else if (hash.startsWith('#/shared')) page = <ExchangesPage onError={onError} />
  else if (hash.startsWith('#/agents')) page = <AgentsPage onError={onError} />
  else if (hash.startsWith('#/recorder')) page = <RecorderPage onError={onError} />
  else if (hash.startsWith('#/policy')) page = <PolicyPage onError={onError} />
  else page = <ApprovalsPage onError={onError} />

  const waiting = status?.holds_pending ?? 0
  return (
    <div className="min-h-screen md:grid md:grid-cols-[15rem_1fr]">
      <aside className="border-b border-line bg-card md:sticky md:top-0 md:h-screen md:border-r md:border-b-0">
        <div className="flex h-full flex-col gap-4 p-4">
          <a href="#/approvals" className="text-2xl font-bold">
            Steward
          </a>
          <nav aria-label="Console" className="flex flex-wrap gap-1 md:flex-col">
            {NAV.map(([href, label]) => {
              const active = hash.startsWith(href) || (href === '#/agents' && !!agentMatch)
              return (
                <a
                  key={href}
                  href={href}
                  aria-current={active ? 'page' : undefined}
                  className={`flex items-center justify-between gap-3 rounded-lg px-3 py-2 ${
                    active ? 'bg-ink text-card' : 'hover:bg-quiet-soft'
                  }`}
                >
                  <span>{label}</span>
                  {href === '#/approvals' && waiting > 0 && (
                    <span className="rounded-full bg-held-soft px-2 text-sm font-bold text-held">{waiting}</span>
                  )}
                </a>
              )
            })}
          </nav>
          <div className="mt-auto hidden space-y-3 md:block">
            {status && <Clock status={status} />}
            {status && (
              <p className="text-sm text-muted">
                {status.head.seq} log entries. Checker: {status.checker}. Web: {status.web_backend}.
              </p>
            )}
            <button
              type="button"
              className="text-sm text-muted underline"
              onClick={() => {
                saveToken(null)
                setSignedIn(false)
              }}
            >
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <main className="mx-auto w-full max-w-5xl px-4 py-8 md:px-8">
        {error && (
          <p className="mb-4 rounded-lg border border-refused bg-refused-soft px-3 py-2 text-refused">
            Steward did not answer: {error}
          </p>
        )}
        {page}
      </main>
    </div>
  )
}

export default App
