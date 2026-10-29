// The public demo's first screen: in three steps, what the personal agent said, what crossed to
// the work agent, and why, then the evening's beats. Everything shown comes from the recording
// and from Steward's own checks run on the server.
import { useEffect, useState } from 'react'
import { api, type CheckResult } from '../api'
import { day, ruleLabel, when } from '../format'
import type { OnError } from '../hooks'
import { useMode } from '../mode'
import { PageTitle, Panel } from '../ui'

type Payload = Record<string, unknown>

function str(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

const FIELD_WORD: Record<string, string> = {
  title: 'the titles',
  location: 'the places',
  note: 'the note',
  reason: 'the reasons',
  home: 'the home address',
}

// The story's beats as sentences (the log names them by story time).
const BEATS: Record<string, string> = {
  '19:00 trip request': 'You ask the work agent to plan the Manchester visit',
  '19:06 approval': 'You approve the train and a stand-in for your address',
  '19:06 after the approvals': 'The work agent books the taxi with the stand-in, never the address',
  '21:15 new mail': 'A phishing email arrives; Steward withholds its link',
  '22:40 new mail': 'A lookalike client email with a hidden instruction arrives',
  '22:41 tricked work agent': 'A tricked work agent asks why each day is blocked, then probes day by day',
  '23:30 change notice sent': 'A blood test lands on the personal calendar; Steward sends only the new busy time',
  '00:50 the work agent marks the expenses done': 'The work agent ticks off a shared to-do item',
  '01:30 the personal agent renames the same item': 'The personal agent writes to the same item from an old copy: held',
  '07:30 morning brief': 'Steward writes the morning brief nobody asked for',
  '07:35 approval': 'You approve the paid changes to the trip',
  '07:35 approvals': 'Your approvals of the paid changes to the trip',
  '19:06 approvals': 'Your approvals of the train and the address',
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-card p-5">
      <p className="text-sm font-bold text-muted">Step {n}</p>
      <h2 className="mt-1 text-xl font-bold">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  )
}

export function StartPage({ onError }: { onError: OnError }) {
  const { info, recording } = useMode()
  const example = info?.examples.reply.find((e) => e.request_type === 'availability') ?? null
  const [checked, setChecked] = useState<CheckResult | null>(null)

  useEffect(() => {
    if (!example) return
    api.demoCheck({ kind: 'reply', ...example }).then(setChecked, onError)
  }, [example, onError])

  const reply = (example?.reply ?? {}) as Payload
  const blocks = Array.isArray(reply.blocks) ? (reply.blocks as Payload[]) : []
  const crossed = ((checked?.crossed ?? {}) as Payload).blocks as Payload[] | undefined
  const dropped = (checked?.dropped_fields as string[] | undefined) ?? []
  const planted = (checked?.planted_in_reply as string[] | undefined) ?? []
  const leaked = (checked?.planted_crossed as string[] | undefined) ?? []
  const beats = (recording?.beats ?? []).filter((b, i, all) => i === 0 || all[i - 1].beat !== b.beat)

  return (
    <div className="space-y-4">
      <PageTitle
        title="Share the constraint, not the reason"
        lead="One person runs two AI agents: a work agent with the work mail and calendar, and a personal agent with the private ones. Steward sits between them and everything they can reach. On Sunday evening the work agent plans a client trip to Manchester and asks the personal agent when the owner is free."
      />
      <Step
        n={1}
        title={
          example && recording && example.recording !== recording.name
            ? 'What a careless personal agent answered'
            : 'What the personal agent answered'
        }
      >
        {example ? (
          <>
            {recording && example.recording !== recording.name && (
              <p className="mb-3 text-sm text-muted">
                This answer is from the scripted evening, where a careless personal agent sends
                everything it has, which shows best what Steward drops. Pick that evening above to
                see it in its own log.
              </p>
            )}
            <ul className="space-y-1">
              {blocks.map((b, i) => (
                <li key={i}>
                  <span className="tabular-nums">
                    {day(str(b.date))}, {str(b.start)} to {str(b.end)}
                  </span>
                  {b.title ? <span className="font-bold">, {str(b.title)}</span> : null}
                  {b.location ? <span>, {str(b.location)}</span> : null}
                </li>
              ))}
            </ul>
            {reply.note ? <p className="mt-2">Note: "{str(reply.note)}"</p> : null}
            <p className="mt-3 text-sm text-muted">
              Private details in this answer: {planted.length ? planted.join(', ') : 'none'}.
            </p>
          </>
        ) : (
          <p className="text-muted">This recording has no availability answer.</p>
        )}
      </Step>
      <Step n={2} title="What crossed to the work agent">
        {checked ? (
          <>
            <ul className="space-y-1">
              {(crossed ?? []).map((b, i) => (
                <li key={i} className="tabular-nums">
                  {day(str(b.date))}, {str(b.start)} to {str(b.end)}, busy, {b.can_move ? 'can move' : "can't move"}
                </li>
              ))}
            </ul>
            <p className="mt-3 rounded-lg bg-trimmed-soft px-3 py-2 text-trimmed">
              Private details that crossed: {leaked.length ? leaked.join(', ') : 'none'}.
            </p>
          </>
        ) : (
          <p className="text-muted">Running Steward's check...</p>
        )}
      </Step>
      <Step n={3} title="Why">
        <ul className="list-disc space-y-1 pl-5">
          <li>
            The work agent may only ask four typed questions (availability, travel limits, dietary and
            access needs, an address stand-in for one booking). Anything else is refused.
          </li>
          <li>
            Code trims every answer to its type: {dropped.length ? `${dropped.map((f) => FIELD_WORD[f] ?? f).join(', ')} never cross` : 'only times and whether they can move cross'}
            {checked?.detail_minutes ? `, and times widen to ${str(checked.detail_minutes)} minutes` : ''}.
          </li>
          {((checked?.rules_fired as string[] | undefined) ?? []).length > 0 && (
            <li>Rules that fired: {((checked?.rules_fired as string[]) ?? []).map(ruleLabel).join(', ')}.</li>
          )}
          <li>No model makes these decisions. The same reply always gives the same answer, which is why the evening replays exactly.</li>
        </ul>
      </Step>
      <Panel title="The rest of the night">
        <ol className="space-y-1">
          {beats.map((b, i) => (
            <li key={i} className="flex gap-3">
              <span className="w-24 shrink-0 tabular-nums text-muted">{when(b.ts).slice(0, 9)}</span>
              <span>
                {BEATS[b.beat] ?? b.beat.replace(/^\d\d:\d\d /, '')}
                {b.actor === 'scripted' ? <span className="text-muted"> (scripted actor)</span> : null}
              </span>
            </li>
          ))}
        </ol>
        <div className="mt-4 flex flex-wrap gap-2">
          <a href="#/activity" className="rounded-lg bg-ink px-4 py-2 font-bold text-card">
            Play the night
          </a>
          <a href="#/today" className="rounded-lg border border-line px-4 py-2 font-bold hover:bg-quiet-soft">
            Read the morning brief
          </a>
          <a href="#/playground" className="rounded-lg border border-line px-4 py-2 font-bold hover:bg-quiet-soft">
            Try it yourself
          </a>
        </div>
      </Panel>
    </div>
  )
}
