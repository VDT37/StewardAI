// Small building blocks shared by the console pages.
import { useState, type ReactNode } from 'react'
import type { LogEvent } from './api'
import { describe, ruleLabel, when, type Outcome } from './format'
import { DEMO_REASON, useMode } from './mode'

export function Panel({
  title,
  aside,
  children,
  className = '',
}: {
  title?: ReactNode
  aside?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`rounded-xl border border-line bg-card p-5 ${className}`}>
      {(title || aside) && (
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          {title && <h2 className="text-lg font-bold">{title}</h2>}
          {aside}
        </div>
      )}
      {children}
    </section>
  )
}

export function PageTitle({ title, lead }: { title: string; lead?: ReactNode }) {
  return (
    <header className="mb-6 max-w-3xl">
      <h1 className="text-[1.75rem] font-bold leading-tight">{title}</h1>
      {lead && <p className="mt-2 text-muted">{lead}</p>}
    </header>
  )
}

const AGENT_STYLE: Record<string, string> = {
  'Work agent': 'bg-work-soft text-work',
  'Personal agent': 'bg-personal-soft text-personal',
}

export function Actor({ name }: { name: string }) {
  const style = AGENT_STYLE[name] ?? 'bg-quiet-soft text-muted'
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-sm font-bold ${style}`}>
      {name}
    </span>
  )
}

const OUTCOME_STYLE: Record<Outcome, string> = {
  allowed: 'bg-quiet-soft text-muted',
  held: 'bg-held-soft text-held',
  refused: 'bg-refused-soft text-refused',
  trimmed: 'bg-trimmed-soft text-trimmed',
  owner: 'bg-work-soft text-work',
  note: '',
}

const OUTCOME_WORD: Record<Outcome, string> = {
  allowed: 'Allowed',
  held: 'Held for you',
  refused: 'Refused',
  trimmed: 'Trimmed',
  owner: 'Your decision',
  note: '',
}

export function OutcomeTag({ outcome, rule }: { outcome: Outcome; rule?: string }) {
  if (outcome === 'note') return null
  return (
    <span className={`inline-flex flex-wrap items-center gap-x-1.5 rounded-md px-2 py-0.5 text-sm ${OUTCOME_STYLE[outcome]}`}>
      <span className="font-bold">{OUTCOME_WORD[outcome]}</span>
      {rule && <span>{ruleLabel(rule)}</span>}
    </span>
  )
}

export function Fingerprint({ value, big = false }: { value: string; big?: boolean }) {
  return (
    <span className={`font-mono ${big ? 'text-2xl tracking-wide' : 'text-sm'}`} title={value}>
      {value.slice(0, 4)}...{value.slice(-3)}
    </span>
  )
}

// owner marks an action only the owner may take: in the public demo it is shown but disabled,
// with the reason as its tooltip.
export function Button({
  children,
  onClick,
  kind = 'plain',
  disabled = false,
  owner = false,
}: {
  children: ReactNode
  onClick: () => void
  kind?: 'primary' | 'plain' | 'danger'
  disabled?: boolean
  owner?: boolean
}) {
  const { demo } = useMode()
  const locked = owner && demo
  const styles = {
    primary: 'bg-ink text-card hover:opacity-90',
    plain: 'border border-line bg-card hover:bg-quiet-soft',
    danger: 'border border-refused text-refused hover:bg-refused-soft',
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || locked}
      title={locked ? DEMO_REASON : undefined}
      className={`rounded-lg px-4 py-2 font-bold disabled:cursor-not-allowed disabled:opacity-50 ${styles[kind]}`}
    >
      {children}
    </button>
  )
}

// In the demo, a one-line reason beside a group of owner buttons.
export function OwnerOnly() {
  const { demo } = useMode()
  if (!demo) return null
  return <p className="text-sm text-muted">{DEMO_REASON}</p>
}

// Which model does what: from the recording in the demo, from the policy for the owner.
export function ModelsStrip({ models }: { models: [string, string][] }) {
  return (
    <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
      {models.map(([role, model]) => (
        <div key={role} className="flex gap-1.5">
          <dt className="text-muted">{role}:</dt>
          <dd className="font-bold">{model}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-6 py-10 text-center">
      <p className="text-lg font-bold">{title}</p>
      {children && <p className="mx-auto mt-2 max-w-xl text-muted">{children}</p>}
    </div>
  )
}

export function Loading() {
  return <p className="text-muted">Loading...</p>
}

export function RawJson({ value, label = 'Show the exact data' }: { value: unknown; label?: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-2">
      <button type="button" className="text-sm text-work underline" onClick={() => setOpen(!open)}>
        {open ? 'Hide the exact data' : label}
      </button>
      {open && (
        <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-quiet-soft p-3 font-mono text-sm leading-relaxed">
          {JSON.stringify(value, null, 2)}
        </pre>
      )}
    </div>
  )
}

export function Spend({ spent, budget }: { spent: string; budget: string }) {
  const share = Math.min(100, (Number(spent) / Math.max(Number(budget), 1e-9)) * 100)
  const over = share >= 100
  return (
    <div>
      <div className="flex justify-between gap-2">
        <span>Model spend today</span>
        <span className="tabular-nums">
          ${Number(spent).toFixed(4)} of ${Number(budget).toFixed(2)}
        </span>
      </div>
      <div className="mt-1.5 h-2 rounded-full bg-quiet-soft">
        <div
          className={`h-2 rounded-full ${over ? 'bg-refused' : 'bg-personal'}`}
          style={{ width: `${Math.max(share, 1)}%` }}
        />
      </div>
      <p className="mt-1 text-sm text-muted">
        {over ? 'Budget used up: new model calls are refused.' : 'Model calls are refused once the budget is used up.'}
      </p>
    </div>
  )
}

// One row of the log: when, who, what happened in a sentence, and how it ended.
export function EventLine({ event, showSession = false }: { event: LogEvent; showSession?: boolean }) {
  const [open, setOpen] = useState(false)
  const line = describe(event)
  return (
    <li className="border-b border-line last:border-0">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full flex-col gap-1 py-3 text-left sm:flex-row sm:items-start sm:gap-4"
      >
        <span className="shrink-0 tabular-nums text-muted sm:w-32">{when(event.ts)}</span>
        <span className="shrink-0 sm:w-36">
          <Actor name={line.actor} />
        </span>
        <span className="flex-1">
          <span className="block">{line.text}</span>
          <span className="mt-1 flex flex-wrap gap-2">
            <OutcomeTag outcome={line.outcome} rule={line.rule} />
          </span>
        </span>
      </button>
      {open && (
        <div className="mb-3 rounded-lg bg-quiet-soft p-3 text-sm">
          <p>
            Event {event.seq}, {event.kind}
            {showSession ? `, session ${event.session_id}` : ''}
          </p>
          <p className="mt-1 break-all text-muted">
            Hash <span className="font-mono">{event.hash}</span>
          </p>
          <RawJson value={event.payload} label="Show the logged data" />
        </div>
      )}
    </li>
  )
}
