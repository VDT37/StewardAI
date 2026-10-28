// The approvals queue: everything Steward is holding for the owner, one pass per call.
import { useState } from 'react'
import { api, ApiError, type Hold } from '../api'
import { askPhrase, callPhrase, day, ruleLabel, when, who } from '../format'
import { useLoad, type OnError } from '../hooks'
import { useMode } from '../mode'
import { Actor, Button, Empty, Fingerprint, OutcomeTag, OwnerOnly, PageTitle, Panel, RawJson } from '../ui'

type Payload = Record<string, unknown>

function str(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

const loadPending = () => api.holds('pending')
const loadAll = () => api.holds('all')

// The sentence at the top of a pass: what the agent wants to do.
function wants(hold: Hold): string {
  if (hold.kind === 'pause') {
    const breach = ((hold.details.breach ?? {}) as Payload).detail
    return `was paused by the watcher: ${str(breach) || ruleLabel(hold.rule)}`
  }
  if (hold.kind === 'address') {
    const request = (hold.details.request ?? {}) as Payload
    return `wants your home address to book a taxi on ${day(str(request.trip_date))}, pickup ${str(request.pickup_time).slice(0, 5)}`
  }
  if (hold.kind === 'ask') {
    return `wants to ask the ${str(hold.args.contact)} agent for ${askPhrase(str(hold.args.request_type), hold.args.fields)}`
  }
  return `wants to ${callPhrase(hold.tool, hold.args)}`
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-sm text-muted">{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

function Facts({ hold }: { hold: Hold }) {
  const d = hold.details as Payload
  const rows: React.ReactNode[] = []
  if (hold.kind === 'address' && d.card) {
    rows.push(
      <Detail key="note" label="The agent's note">
        {str((d.request as Payload | undefined)?.booking_summary)}
      </Detail>,
    )
  }
  const quote = d.quote as Payload | undefined
  if (quote) {
    rows.push(
      <Detail key="price" label="Price">
        £{str(quote.price_gbp)} for the trip on {day(str(quote.trip_date))}
        {d.trip_total_gbp !== undefined &&
          `; £${str(d.trip_total_gbp)} already booked, limit £${str(d.trip_limit_gbp)}`}
      </Detail>,
    )
  }
  const booking = d.booking as Payload | undefined
  if (booking) {
    rows.push(
      <Detail key="booking" label="Booking now">
        {booking.kind === 'train'
          ? `Train ${str(booking.depart)} to ${str(booking.arrive)}, £${str(booking.price_gbp)}`
          : `Taxi, pickup ${str(booking.pickup_time)}, £${str(booking.price_gbp)}`}
      </Detail>,
    )
  }
  const current = d.current as Payload | undefined
  const proposed = d.proposed as Payload | undefined
  if (current && proposed) {
    rows.push(
      <Detail key="versions" label="Both versions">
        <div className="mt-1 grid gap-2 sm:grid-cols-2">
          <div className="rounded-lg border border-line p-3">
            <p className="font-bold">Now, version {str(current.version)}</p>
            <p>{str(current.title)}</p>
            <p className="text-muted">{current.done ? 'Done' : 'Not done'}</p>
          </div>
          <div className="rounded-lg border border-held-line p-3">
            <p className="font-bold">This write, made from version {str(proposed.read_version)}</p>
            <p>{str((proposed.fields as Payload | undefined)?.title) || str(current.title)}</p>
            <p className="text-muted">
              {(proposed.fields as Payload | undefined)?.done === true ? 'Done' : 'Unchanged'}
            </p>
          </div>
        </div>
      </Detail>,
    )
  }
  const flags = d.flags as Payload | undefined
  if (flags && hold.rules.includes('trifecta')) {
    rows.push(
      <Detail key="flags" label="Why the agent is flagged">
        <p>Read your private data: {(flags.private_read as string[]).join(', ') || 'nothing yet'}</p>
        <p>Saw untrusted content: {(flags.untrusted_seen as string[]).join(', ') || 'nothing yet'}</p>
      </Detail>,
    )
  }
  if (hold.kind === 'ask' && d.asks_today !== undefined) {
    rows.push(
      <Detail key="count" label="Asks today">
        {str(d.asks_today)} of {str(d.limit)}
      </Detail>,
    )
  }
  rows.push(
    <Detail key="expires" label="Waits until">
      {when(hold.expires)}, then nothing happens
    </Detail>,
  )
  return <dl className="mt-4 space-y-3">{rows}</dl>
}

function Pass({ hold, onDecided, onError }: { hold: Hold; onDecided: () => void; onError: OnError }) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const decide = (choice: string) => {
    setBusy(true)
    setProblem(null)
    api.decide(hold, choice).then(
      () => {
        setBusy(false)
        onDecided()
      },
      (error: unknown) => {
        setBusy(false)
        if (error instanceof ApiError) setProblem(error.message)
        else onError(error)
      },
    )
  }
  return (
    <article className="arrive grid overflow-visible rounded-xl border border-held-line bg-card lg:grid-cols-[1fr_16rem]">
      <div className="p-5">
        <div className="flex flex-wrap items-center gap-2">
          <Actor name={who(hold.agent)} />
          <OutcomeTag outcome="held" rule={hold.rule} />
        </div>
        <p className="mt-3 text-xl font-bold leading-snug">
          The {who(hold.agent).toLowerCase()} {wants(hold)}.
        </p>
        <p className="mt-2">
          {hold.reason.charAt(0).toUpperCase()}
          {hold.reason.slice(1)}.
        </p>
        {hold.rules.length > 1 && (
          <p className="mt-1 text-sm text-muted">Rules: {hold.rules.map(ruleLabel).join(', ')}.</p>
        )}
        <Facts hold={hold} />
        <RawJson value={{ agent: hold.agent, tool: hold.tool, args: hold.args, hold_id: hold.hold_id }} />
      </div>
      <div className="pass-stub flex flex-col justify-between gap-4 bg-held-soft p-5">
        <div>
          <p className="text-muted">Locked to this exact request</p>
          <p className="mt-1">
            <Fingerprint value={hold.fingerprint} big />
          </p>
          <p className="mt-2 text-sm text-muted">Any change to the call voids your approval.</p>
        </div>
        <div className="flex flex-col gap-2">
          {hold.choices.map((choice, index) => (
            <Button
              key={choice.id}
              kind={index === 0 ? 'primary' : choice.id === 'deny' ? 'danger' : 'plain'}
              owner
              disabled={busy}
              onClick={() => decide(choice.id)}
            >
              {choice.label}
            </Button>
          ))}
          <OwnerOnly />
          {problem && <p className="text-sm text-refused">{problem}</p>}
        </div>
      </div>
    </article>
  )
}

const STATE_WORD: Record<string, string> = {
  done: 'Approved and done once',
  denied: 'You said no',
  expired: 'Expired, nothing ran',
  failed: 'Approved, but it failed',
  unknown: 'Approved, outcome unknown',
  approved: 'Approved, running',
}

export function ApprovalsPage({ onError }: { onError: OnError }) {
  const { demo } = useMode()
  const [pending, reloadPending] = useLoad(loadPending, onError, 2500)
  const [all, reloadAll] = useLoad(loadAll, onError, 6000)
  const decided = (all ?? []).filter((h) => h.state !== 'pending').slice(0, 12)
  const refresh = () => {
    reloadPending()
    reloadAll()
  }
  return (
    <div>
      <PageTitle
        title={demo ? 'Held for the owner' : 'Waiting for you'}
        lead="Steward holds a call when it needs the owner's yes: sending, paid changes, the home address, anything after an agent has seen untrusted content, and writes made from an old version. The watcher's pauses wait here too."
      />
      {pending === null ? null : pending.length === 0 ? (
        <Empty title="Nothing is waiting for you">
          Held calls appear here the moment an agent makes one. The agents carry on with other
          work in the meantime.
        </Empty>
      ) : (
        <div className="space-y-6">
          {pending.map((hold) => (
            <Pass key={hold.hold_id} hold={hold} onDecided={refresh} onError={onError} />
          ))}
        </div>
      )}
      {decided.length > 0 && (
        <Panel title="Decided recently" className="mt-8">
          <ul>
            {decided.map((hold) => (
              <li key={hold.hold_id} className="flex flex-col gap-1 border-b border-line py-3 last:border-0 sm:flex-row sm:gap-4">
                <span className="shrink-0 tabular-nums text-muted sm:w-32">{when(hold.created)}</span>
                <span className="flex-1">
                  The {who(hold.agent).toLowerCase()} {wants(hold)}.{' '}
                  <span className="text-muted">{STATE_WORD[hold.state] ?? hold.state}.</span>
                  {hold.result && 'address' in hold.result && (
                    <span> Stand-in {str(hold.result.address)}.</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  )
}
