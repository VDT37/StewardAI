// The flight recorder: the hash chain, the export, the Manchester evening and its replay.
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type ClockInfo, type ReplayResult, type ScenarioState, type VerifyResult } from '../api'
import { when } from '../format'
import { useLoad, type OnError } from '../hooks'
import { Button, PageTitle, Panel, RawJson } from '../ui'

const loadScenario = () => api.scenario()
const loadReplay = () => api.lastReplay()
const loadClock = () => api.clock()

function str(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

function Chain({ onError }: { onError: OnError }) {
  const [result, setResult] = useState<VerifyResult | null>(null)
  const [checking, setChecking] = useState(true)
  const run = useCallback(() => {
    api
      .verify()
      .then(setResult, onError)
      .finally(() => setChecking(false))
  }, [onError])
  useEffect(run, [run])
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
    <Panel
      title="Chain check"
      aside={
        <div className="flex gap-2">
          <Button
            onClick={() => {
              setChecking(true)
              run()
            }}
            disabled={checking}
          >
            {checking ? 'Checking...' : 'Check again'}
          </Button>
          <Button onClick={download}>Download the log</Button>
        </div>
      }
    >
      <p className="max-w-3xl text-muted">
        Every entry stores the hash of the one before it, so changing any entry breaks the chain
        from that point on. The check recomputes every hash from the first entry.
      </p>
      {result && (
        <p className={`mt-3 text-lg font-bold ${result.ok ? 'text-personal' : 'text-refused'}`}>
          {result.ok
            ? `Intact: ${result.rows} entries, nothing changed.`
            : `Broken at entry ${result.first_bad_seq}: ${result.reason}.`}
        </p>
      )}
    </Panel>
  )
}

function StoryClock({ onError }: { onError: OnError }) {
  const [clock, reload] = useLoad<ClockInfo>(loadClock, onError, 5000)
  if (!clock || clock.mode !== 'story') return null
  return (
    <div className="mt-4 rounded-lg bg-work-soft p-4 text-work">
      <p>
        Steward is on story time ({when(clock.now)}), so anything still held from the evening can
        be decided. Going back to real time ends the story: what is still held expires.
      </p>
      <div className="mt-2">
        <Button onClick={() => api.resetClock().then(reload, onError)}>Back to real time</Button>
      </div>
    </div>
  )
}

function Scenario({ onError }: { onError: OnError }) {
  const [state, reload] = useLoad<ScenarioState>(loadScenario, onError, 3000)
  const [problem, setProblem] = useState<string | null>(null)
  const start = (approvals: 'scripted' | 'manual') => {
    setProblem(null)
    api.runScenario(approvals).then(reload, (error: unknown) => {
      if (error instanceof ApiError) setProblem(error.message)
      else onError(error)
    })
  }
  const running = state?.state === 'running' || state?.state === 'starting'
  const beats = (state?.beats ?? []).filter((b) => !str(b.beat).endsWith('(finished)'))
  return (
    <Panel title="The Manchester evening">
      <p className="max-w-3xl text-muted">
        Plays Sunday 19:00 to Monday 07:35 live with both agents: the trip request, the poisoned
        page and email, the blood test, the two to-do writes and your approvals. Steward's clock
        runs on story time while it plays.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button kind="primary" onClick={() => start('scripted')} disabled={running}>
          Play with scripted approvals
        </Button>
        <Button onClick={() => start('manual')} disabled={running}>
          Play and let me approve
        </Button>
      </div>
      {problem && <p className="mt-2 text-refused">{problem}</p>}
      <StoryClock onError={onError} />
      {state && state.state !== 'idle' && (
        <div className="mt-4">
          <p className="font-bold">
            {state.state === 'done' ? 'Finished' : state.state === 'failed' ? `Stopped: ${state.error}` : 'Running'}
            {state.session_id && <span className="font-normal text-muted"> (session {state.session_id})</span>}
          </p>
          <ol className="mt-2 space-y-1">
            {beats.map((beat, i) => (
              <li key={i}>
                {str(beat.beat)}
                {beat.actor === 'scripted' && <span className="text-muted"> (scripted)</span>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </Panel>
  )
}

function Replay({ onError }: { onError: OnError }) {
  const [last, reload] = useLoad<ReplayResult>(loadReplay, onError, 10000)
  const [running, setRunning] = useState(false)
  const run = () => {
    setRunning(true)
    api
      .replay()
      .then(reload, onError)
      .finally(() => setRunning(false))
  }
  const result = last && last.state !== 'none' ? last : null
  return (
    <Panel
      title="Replay"
      aside={
        <Button kind="primary" onClick={run} disabled={running}>
          {running ? 'Replaying...' : 'Replay the last evening'}
        </Button>
      }
    >
      <p className="max-w-3xl text-muted">
        Replay plays the recorded actions back through a fresh Steward, with no agents, no model
        calls and no keys, and compares every decision with the recording.
      </p>
      {result && (
        <div className="mt-3">
          {result.error ? (
            <p className="text-refused">{result.error}</p>
          ) : (
            <p className={`text-lg font-bold ${result.ok ? 'text-personal' : 'text-refused'}`}>
              {result.ok
                ? `Identical: ${result.compared} decisions matched in ${result.seconds} s.`
                : `Different at decision ${result.first_difference?.index} of ${result.recorded_decisions}.`}
            </p>
          )}
          {result.first_difference && <RawJson value={result.first_difference} label="Show the first difference" />}
        </div>
      )}
    </Panel>
  )
}

export function RecorderPage({ onError }: { onError: OnError }) {
  return (
    <div className="space-y-4">
      <PageTitle title="Recorder" lead="The flight log is append-only and hash-chained. Replay shows Steward makes the same decisions every time." />
      <Chain onError={onError} />
      <Scenario onError={onError} />
      <Replay onError={onError} />
    </div>
  )
}
