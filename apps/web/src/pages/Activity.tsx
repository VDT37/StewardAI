// The live log as sentences: what each agent did and how Steward decided, newest first.
import { useEffect, useMemo, useState } from 'react'
import { api, followLog, type LogEvent } from '../api'
import { isDecision } from '../format'
import { type OnError } from '../hooks'
import { useMode } from '../mode'
import { EventLine, PageTitle, Panel } from '../ui'

const WHO = [
  ['', 'Everyone'],
  ['work', 'Work agent'],
  ['personal', 'Personal agent'],
] as const

// A story beat ("19:00 trip request") as a divider between the events it led to.
function Beat({ event }: { event: LogEvent }) {
  return (
    <li className="border-b border-line pt-6 pb-2">
      <p className="text-lg font-bold">{String(event.payload.beat)}</p>
      {event.payload.actor === 'scripted' && (
        <p className="text-sm text-muted">Played by the script, as the overnight script says.</p>
      )}
    </li>
  )
}

export function ActivityPage({ onError }: { onError: OnError }) {
  const { demo } = useMode()
  const [events, setEvents] = useState<LogEvent[]>([])
  const [live, setLive] = useState(false)
  const [agent, setAgent] = useState('')
  const [everything, setEverything] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    let stopped = false
    api.log({ limit: 500 }).then((initial) => {
      if (stopped) return
      setEvents(initial)
      // A recording does not change: nothing to follow.
      if (demo) return
      const after = initial.length ? initial[0].seq : 0
      setLive(true)
      followLog(
        after,
        (event) => setEvents((current) => [event, ...current].slice(0, 3000)),
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
  }, [onError, demo])

  const shown = useMemo(
    () =>
      events.filter((e) => {
        if (agent && e.agent !== agent) return false
        if (e.kind === 'scenario.beat') return !String(e.payload.beat).endsWith('(finished)')
        return everything || isDecision(e)
      }),
    [events, agent, everything],
  )

  return (
    <div>
      <PageTitle
        title="Activity"
        lead="Every model call, tool call and message between the agents goes through Steward and is written here. Click a line for the logged data."
      />
      <Panel>
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <span className={live ? 'font-bold text-personal' : 'text-muted'}>
            {demo ? 'Recorded evening' : live ? 'Live' : 'Not connected'}
          </span>
          <div className="flex flex-wrap gap-1" role="group" aria-label="Show events for">
            {WHO.map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={agent === value}
                onClick={() => setAgent(value)}
                className={`rounded-lg px-3 py-1.5 ${agent === value ? 'bg-ink text-card' : 'border border-line hover:bg-quiet-soft'}`}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="ml-auto flex items-center gap-2">
            <input
              type="checkbox"
              checked={everything}
              onChange={(e) => setEverything(e.target.checked)}
              className="h-4 w-4"
            />
            Also show model calls, results and deliveries
          </label>
        </div>
        {shown.length === 0 ? (
          <p className="py-6 text-muted">Nothing yet. Activity appears here as the agents work.</p>
        ) : (
          <ul>
            {shown.map((event) =>
              event.kind === 'scenario.beat' ? (
                <Beat key={event.seq} event={event} />
              ) : (
                <EventLine key={event.seq} event={event} />
              ),
            )}
          </ul>
        )}
      </Panel>
    </div>
  )
}
