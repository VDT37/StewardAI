// Steward console (Phase 3): Today (the morning brief) first, then approvals, what the agents
// did, what crossed between them, the agents, the recorder and the policy. The owner reaches it
// through an SSH tunnel. The public demo serves the same build over recorded evenings, read-only:
// it skips sign-in, starts on a guided first screen and adds the Playground.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, isAuthError, saveToken, savedToken, setBase, type DemoInfo, type DemoRecording, type Status } from './api'
import { modelName, when } from './format'
import { ModeContext, type Mode } from './mode'
import { ActivityPage } from './pages/Activity'
import { AgentPage, AgentsPage } from './pages/Agents'
import { ApprovalsPage } from './pages/Approvals'
import { ExchangesPage } from './pages/Exchanges'
import { LimitsPage } from './pages/Limits'
import { PlaygroundPage } from './pages/Playground'
import { PolicyPage } from './pages/Policy'
import { RecorderPage } from './pages/Recorder'
import { StartPage } from './pages/Start'
import { TodayPage } from './pages/Today'
import { ModelsStrip } from './ui'

type Boot = { kind: 'loading' } | { kind: 'owner' } | { kind: 'demo'; info: DemoInfo }

// The demo server answers /demo/info; the owner's Steward does not.
function useBoot(): Boot {
  const [boot, setBoot] = useState<Boot>({ kind: 'loading' })
  useEffect(() => {
    fetch('/demo/info').then(
      async (response) => {
        if (!response.ok) return setBoot({ kind: 'owner' })
        setBoot({ kind: 'demo', info: (await response.json()) as DemoInfo })
      },
      () => setBoot({ kind: 'owner' }),
    )
  }, [])
  return boot
}

// The hash route, or fallback when there is none (the fallback depends on the mode, which is
// known only after boot).
function useHashRoute(fallback: string): string {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const update = () => setHash(window.location.hash)
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  return hash || fallback
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

const OWNER_NAV = [
  ['#/today', 'Today'],
  ['#/approvals', 'Waiting for you'],
  ['#/activity', 'Activity'],
  ['#/shared', 'Shared and held'],
  ['#/agents', 'Agents'],
  ['#/recorder', 'Recorder'],
  ['#/policy', 'Policy'],
] as const

const DEMO_NAV = [
  ['#/start', 'Start here'],
  ['#/today', 'Morning brief'],
  ['#/approvals', 'Held for the owner'],
  ['#/activity', 'Activity'],
  ['#/shared', 'Shared and held'],
  ['#/agents', 'Agents'],
  ['#/playground', 'Playground'],
  ['#/recorder', 'Recorder'],
  ['#/policy', 'Policy'],
  ['#/limits', 'Limits'],
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

function recordingModels(rec: DemoRecording): [string, string][] {
  const used = (agent: string) => {
    const names = rec.models_used[agent] ?? []
    return names.length ? names.map(modelName).join(', ') : 'scripted actor, no model'
  }
  const brief = rec.brief?.source === 'model' ? modelName(rec.brief.model) : 'the template (code)'
  return [
    ['Work agent', used('work')],
    ['Personal agent', used('personal')],
    ['Morning brief', brief],
    ['Every decision', 'code, no model'],
  ]
}

const RECORDING_KEY = 'steward-demo-recording'

function remembered(): string | null {
  try {
    return localStorage.getItem(RECORDING_KEY)
  } catch {
    return null
  }
}

function DemoBanner({
  info,
  recording,
  onPick,
}: {
  info: DemoInfo
  recording: DemoRecording
  onPick: (name: string) => void
}) {
  return (
    <div className="mb-6 rounded-xl border border-work bg-work-soft p-4 text-ink">
      <p className="font-bold text-work">
        A recorded evening. Nothing here calls a model or changes anything.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <label htmlFor="recording" className="text-sm text-muted">
          Evening
        </label>
        <select
          id="recording"
          value={recording.name}
          onChange={(event) => onPick(event.target.value)}
          className="rounded-lg border border-line bg-card px-3 py-1.5"
        >
          {info.recordings.map((rec) => (
            <option key={rec.name} value={rec.name}>
              {rec.label}
            </option>
          ))}
        </select>
        <span className="text-sm text-muted">
          {recording.kind === 'live'
            ? `Real agents on Nemotron, recorded ${recording.recorded_on ?? ''}`
            : 'No model: scripted actors play the agents through the same doors'}
        </span>
      </div>
      <div className="mt-3">
        <ModelsStrip models={recordingModels(recording)} />
      </div>
    </div>
  )
}

function App() {
  const boot = useBoot()
  const demo = boot.kind === 'demo'
  const info = boot.kind === 'demo' ? boot.info : null
  const [picked, setPicked] = useState<string | null>(remembered)
  const recording = useMemo(() => {
    if (!info) return null
    return info.recordings.find((r) => r.name === picked) ?? info.recordings.find((r) => r.name === info.default) ?? info.recordings[0] ?? null
  }, [info, picked])
  setBase(recording ? `/r/${recording.name}` : '')

  const [signedIn, setSignedIn] = useState(() => !!savedToken())
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)
  const hash = useHashRoute(demo ? '#/start' : '#/today')

  const onError = useCallback((reason: unknown) => {
    if (isAuthError(reason)) {
      saveToken(null)
      setSignedIn(false)
    } else {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }, [])

  const ready = demo ? recording !== null : signedIn
  useEffect(() => {
    if (!ready || boot.kind === 'loading') return
    const load = () =>
      api.status().then((value) => {
        setStatus(value)
        setError(null)
      }, onError)
    load()
    const timer = setInterval(load, demo ? 15000 : 2500)
    return () => clearInterval(timer)
  }, [ready, demo, boot.kind, recording, onError])

  if (boot.kind === 'loading') return null
  if (!demo && !signedIn) return <SignIn onDone={() => setSignedIn(true)} />
  if (demo && !recording) {
    return (
      <main className="mx-auto max-w-xl px-4 py-24">
        <h1 className="text-3xl font-bold">Steward</h1>
        <p className="mt-3 text-muted">The public demo has no recording loaded yet. Try again in a minute.</p>
      </main>
    )
  }

  const pick = (name: string) => {
    try {
      localStorage.setItem(RECORDING_KEY, name)
    } catch {
      // the choice is just not remembered
    }
    setStatus(null)
    setPicked(name)
  }

  const mode: Mode = { demo, info, recording }
  const nav = demo ? DEMO_NAV : OWNER_NAV
  const agentMatch = hash.match(/^#\/agents\/([\w-]+)/)
  let page
  if (agentMatch) page = <AgentPage key={agentMatch[1]} id={agentMatch[1]} onError={onError} />
  else if (hash.startsWith('#/approvals')) page = <ApprovalsPage onError={onError} />
  else if (hash.startsWith('#/activity')) page = <ActivityPage onError={onError} />
  else if (hash.startsWith('#/shared')) page = <ExchangesPage onError={onError} />
  else if (hash.startsWith('#/agents')) page = <AgentsPage onError={onError} />
  else if (hash.startsWith('#/recorder')) page = <RecorderPage onError={onError} />
  else if (hash.startsWith('#/policy')) page = <PolicyPage onError={onError} />
  else if (demo && hash.startsWith('#/playground')) page = <PlaygroundPage onError={onError} />
  else if (demo && hash.startsWith('#/limits')) page = <LimitsPage />
  else if (demo && hash.startsWith('#/start')) page = <StartPage onError={onError} />
  else page = <TodayPage onError={onError} />

  const waiting = status?.holds_pending ?? 0
  return (
    <ModeContext.Provider value={mode}>
      <div className="min-h-screen md:grid md:grid-cols-[15rem_1fr]">
        <aside className="border-b border-line bg-card md:sticky md:top-0 md:h-screen md:border-r md:border-b-0">
          <div className="flex h-full flex-col gap-4 p-4">
            <a href={demo ? '#/start' : '#/today'} className="text-2xl font-bold">
              Steward
            </a>
            <nav aria-label="Console" className="flex flex-wrap gap-1 md:flex-col">
              {nav.map(([href, label]) => {
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
              {!demo && (
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
              )}
              {demo && (
                <a className="block text-sm text-muted underline" href="https://github.com/VDT37/StewardAI">
                  Source on GitHub
                </a>
              )}
            </div>
          </div>
        </aside>
        <main className="mx-auto w-full max-w-5xl px-4 py-8 md:px-8">
          {demo && info && recording && <DemoBanner info={info} recording={recording} onPick={pick} />}
          {error && (
            <p className="mb-4 rounded-lg border border-refused bg-refused-soft px-3 py-2 text-refused">
              Steward did not answer: {error}
            </p>
          )}
          <div key={recording?.name ?? 'owner'}>{page}</div>
        </main>
      </div>
    </ModeContext.Provider>
  )
}

export default App
