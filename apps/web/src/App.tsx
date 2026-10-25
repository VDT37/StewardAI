// Steward console v0 (Phase 1): agents, their passes, tools, spend, the live log and the chain.
// Served by Steward at /console and reached through an SSH tunnel until Phase 3.
import { useCallback, useEffect, useState } from 'react'
import { api, isAuthError, saveToken, savedToken, type Status } from './api'
import { AgentPage, AgentsPage, ChainPage, LogPage } from './pages'

function useHashRoute(): string {
  const [hash, setHash] = useState(window.location.hash || '#/agents')
  useEffect(() => {
    const update = () => setHash(window.location.hash || '#/agents')
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  return hash
}

function SignIn({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState('')
  return (
    <main className="mx-auto max-w-md px-4 py-24">
      <h1 className="text-2xl font-semibold">Steward</h1>
      <p className="mt-2 text-sm text-stone-600 dark:text-stone-400">
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
        <input
          type="password"
          autoComplete="off"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          className="flex-1 rounded border border-stone-300 bg-white px-3 py-2 text-sm dark:border-stone-600 dark:bg-stone-900"
          placeholder="Owner token"
        />
        <button
          type="submit"
          className="rounded bg-stone-900 px-4 py-2 text-sm text-white dark:bg-stone-100 dark:text-stone-900"
        >
          Sign in
        </button>
      </form>
    </main>
  )
}

const NAV = [
  ['#/agents', 'Agents'],
  ['#/log', 'Live log'],
  ['#/chain', 'Chain'],
] as const

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
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [signedIn, onError])

  if (!signedIn) return <SignIn onDone={() => setSignedIn(true)} />

  const agentMatch = hash.match(/^#\/agents\/([\w-]+)/)
  let page
  if (agentMatch) page = <AgentPage key={agentMatch[1]} id={agentMatch[1]} onError={onError} />
  else if (hash.startsWith('#/log')) page = <LogPage onError={onError} />
  else if (hash.startsWith('#/chain')) page = <ChainPage onError={onError} />
  else page = <AgentsPage onError={onError} />

  return (
    <div className="min-h-screen bg-stone-50 text-stone-900 dark:bg-stone-950 dark:text-stone-100">
      <header className="border-b border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <a href="#/agents" className="text-lg font-semibold">
            Steward
          </a>
          <nav className="flex gap-4 text-sm">
            {NAV.map(([href, label]) => (
              <a
                key={href}
                href={href}
                className={
                  hash.startsWith(href) ? 'font-semibold' : 'text-stone-500 hover:text-stone-900'
                }
              >
                {label}
              </a>
            ))}
          </nav>
          {status && (
            <p className="ml-auto text-xs text-stone-500">
              session <span className="font-mono">{status.session_id}</span> · {status.head.seq}{' '}
              events · checker {status.checker} · {status.pending} open asks
            </p>
          )}
          <button
            type="button"
            className="text-xs text-stone-500 hover:text-stone-900"
            onClick={() => {
              saveToken(null)
              setSignedIn(false)
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        {error && (
          <p className="mb-4 rounded border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800">
            {error}
          </p>
        )}
        {page}
      </main>
    </div>
  )
}

export default App
