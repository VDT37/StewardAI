// The playground (public demo): Steward's own checks run on the server on what you type. Edit
// the personal agent's reply, write an attack ask, paste a suspicious email, or change one
// character of a held call. Nothing is stored and no model is called.
import { useState } from 'react'
import { api, ApiError, type CheckResult, type ReplyExample } from '../api'
import { ruleLabel } from '../format'
import type { OnError } from '../hooks'
import { useMode } from '../mode'
import { Button, Fingerprint, PageTitle, Panel } from '../ui'

type Tab = 'reply' | 'ask' | 'mail' | 'lock'

const TABS: [Tab, string][] = [
  ['reply', 'A reply'],
  ['ask', 'An ask'],
  ['mail', 'An email'],
  ['lock', 'An approval'],
]

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

function str(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

function Field({
  id,
  label,
  value,
  onChange,
  rows = 1,
  mono = false,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  rows?: number
  mono?: boolean
}) {
  const style = `mt-1 w-full rounded-lg border border-line bg-card px-3 py-2 ${mono ? 'font-mono text-sm' : ''}`
  return (
    <div>
      <label htmlFor={id} className="font-bold">
        {label}
      </label>
      {rows > 1 ? (
        <textarea id={id} rows={rows} value={value} spellCheck={false} onChange={(e) => onChange(e.target.value)} className={style} />
      ) : (
        <input id={id} value={value} onChange={(e) => onChange(e.target.value)} className={style} />
      )}
    </div>
  )
}

function useCheck(onError: OnError) {
  const [result, setResult] = useState<CheckResult | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const run = (body: Record<string, unknown>) => {
    setBusy(true)
    setProblem(null)
    api.demoCheck(body).then(
      (value) => {
        setResult(value)
        setBusy(false)
      },
      (error: unknown) => {
        setBusy(false)
        if (error instanceof ApiError) setProblem(error.message)
        else onError(error)
      },
    )
  }
  return { result, problem, busy, run, setProblem }
}

function parse(text: string, what: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(`${what} is not valid JSON`)
  }
}

function Verdict({ decision, rule, reason }: { decision: unknown; rule?: unknown; reason?: unknown }) {
  const tone =
    decision === 'allow' ? 'bg-personal-soft text-personal' : decision === 'hold' ? 'bg-held-soft text-held' : 'bg-refused-soft text-refused'
  const word = decision === 'allow' ? 'Allowed' : decision === 'hold' ? 'Held for the owner' : 'Refused'
  return (
    <p className={`rounded-lg px-3 py-2 ${tone}`}>
      <span className="font-bold">{word}</span>
      {rule ? `: ${ruleLabel(str(rule))}` : ''}
      {reason && decision !== 'allow' ? ` (${str(reason)})` : ''}
    </p>
  )
}

function ReplyTab({ examples, onError }: { examples: ReplyExample[]; onError: OnError }) {
  const [index, setIndex] = useState(0)
  const example = examples[index]
  const [request, setRequest] = useState(pretty(example?.request ?? {}))
  const [reply, setReply] = useState(pretty(example?.reply ?? {}))
  const check = useCheck(onError)
  if (!example) return <p className="text-muted">No reply in this recording to start from.</p>
  const choose = (i: number) => {
    setIndex(i)
    setRequest(pretty(examples[i].request))
    setReply(pretty(examples[i].reply))
  }
  const submit = () => {
    try {
      check.run({
        kind: 'reply',
        recording: example.recording,
        request_type: example.request_type,
        request: parse(request, 'The ask'),
        reply: parse(reply, 'The reply'),
      })
    } catch (error) {
      check.setProblem((error as Error).message)
    }
  }
  const r = check.result
  return (
    <div className="space-y-4">
      <p className="text-muted">
        This is what the personal agent really answered in the recording. Add a name, a reason or a
        new field, then check what would cross.
      </p>
      <div className="flex flex-wrap gap-2">
        {examples.map((e, i) => (
          <Button key={e.request_type} kind={i === index ? 'primary' : 'plain'} onClick={() => choose(i)}>
            {e.request_type.replaceAll('_', ' ')}
          </Button>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Field id="ask" label="The work agent asked" value={request} onChange={setRequest} rows={6} mono />
        <Field id="reply" label="The personal agent answers" value={reply} onChange={setReply} rows={14} mono />
      </div>
      <Button kind="primary" onClick={submit} disabled={check.busy}>
        {check.busy ? 'Checking...' : 'Check what crosses'}
      </Button>
      {check.problem && <p className="text-refused">{check.problem}</p>}
      {r && (
        <div className="space-y-3">
          <Verdict decision={r.decision} rule={r.rule} reason={r.reason ?? (r.request as Record<string, unknown> | undefined)?.reason} />
          {r.crossed ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <p className="font-bold">What crosses</p>
                <pre className="mt-1 overflow-auto rounded-lg bg-quiet-soft p-3 font-mono text-sm">{pretty(r.crossed)}</pre>
              </div>
              <div>
                <p className="font-bold">What the work agent reads</p>
                <p className="mt-1 rounded-lg bg-quiet-soft p-3">{str(r.asker_reads)}</p>
              </div>
            </div>
          ) : null}
          <ul className="space-y-1">
            {((r.dropped_fields as string[] | undefined) ?? []).length > 0 && (
              <li>Dropped: {(r.dropped_fields as string[]).join(', ')}.</li>
            )}
            {((r.rules_fired as string[] | undefined) ?? []).length > 0 && (
              <li>Rules that fired: {(r.rules_fired as string[]).map(ruleLabel).join(', ')}.</li>
            )}
            <li>
              Private details in your reply: {((r.planted_in_reply as string[] | undefined) ?? []).join(', ') || 'none'}.
              Crossed: <span className="font-bold">{((r.planted_crossed as string[] | undefined) ?? []).join(', ') || 'none'}</span>.
            </li>
          </ul>
        </div>
      )}
    </div>
  )
}

const ASK_LABELS = ['Ask why each day is blocked (the 22:40 attack)', 'Availability, with an extra field', 'A normal availability ask']

function AskTab({ examples, onError }: { examples: { request_type: string; fields: unknown }[]; onError: OnError }) {
  const [type, setType] = useState(examples[0]?.request_type ?? 'availability')
  const [fields, setFields] = useState(pretty(examples[0]?.fields ?? {}))
  const check = useCheck(onError)
  const choose = (i: number) => {
    setType(examples[i].request_type)
    setFields(pretty(examples[i].fields))
  }
  const submit = () => {
    try {
      check.run({ kind: 'ask', request_type: type, fields: parse(fields, 'The fields') })
    } catch (error) {
      check.setProblem((error as Error).message)
    }
  }
  const r = check.result
  return (
    <div className="space-y-4">
      <p className="text-muted">
        Write the ask a tricked work agent would send. Steward checks the type and every field
        before anything reaches the personal agent, and the personal agent only ever reads a fixed
        message built from the typed fields.
      </p>
      <div className="flex flex-wrap gap-2">
        {examples.map((e, i) => (
          <Button key={i} onClick={() => choose(i)}>
            {ASK_LABELS[i] ?? e.request_type}
          </Button>
        ))}
      </div>
      <Field id="type" label="Request type" value={type} onChange={setType} />
      <Field id="fields" label="Fields" value={fields} onChange={setFields} rows={6} mono />
      <Button kind="primary" onClick={submit} disabled={check.busy}>
        {check.busy ? 'Checking...' : 'Check the ask'}
      </Button>
      {check.problem && <p className="text-refused">{check.problem}</p>}
      {r && (
        <div className="space-y-3">
          <Verdict decision={r.decision} rule={r.rule} reason={r.reason} />
          {r.message_to_personal ? (
            <div>
              <p className="font-bold">What the personal agent would read</p>
              <p className="mt-1 whitespace-pre-wrap rounded-lg bg-quiet-soft p-3">{str(r.message_to_personal)}</p>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

type MailExample = { from: string; subject: string; body: string; body_html: string } | null

function MailTab({ example, onError }: { example: MailExample; onError: OnError }) {
  const [from, setFrom] = useState(example?.from ?? '')
  const [subject, setSubject] = useState(example?.subject ?? '')
  const [body, setBody] = useState(example?.body ?? '')
  const [html, setHtml] = useState(example?.body_html ?? '')
  const check = useCheck(onError)
  const r = check.result
  const reads = (r?.agent_reads ?? {}) as Record<string, unknown>
  const links = (r?.links as { url: string; text: string; host: string }[] | undefined) ?? []
  return (
    <div className="space-y-4">
      <p className="text-muted">
        The 21:15 email of the story is below, made from a real phishing test. Paste one you received
        instead: Steward scores the signs a person is taught to look for, in code.
      </p>
      <Field id="from" label="From" value={from} onChange={setFrom} />
      <Field id="subject" label="Subject" value={subject} onChange={setSubject} />
      <Field id="body" label="Text" value={body} onChange={setBody} rows={6} />
      <Field id="html" label="HTML (optional)" value={html} onChange={setHtml} rows={4} mono />
      <Button
        kind="primary"
        disabled={check.busy}
        onClick={() => check.run({ kind: 'mail', message: { from, subject, body, body_html: html } })}
      >
        {check.busy ? 'Checking...' : 'Screen the email'}
      </Button>
      {check.problem && <p className="text-refused">{check.problem}</p>}
      {r && (
        <div className="space-y-3">
          <p className={`rounded-lg px-3 py-2 font-bold ${r.suspected ? 'bg-refused-soft text-refused' : 'bg-personal-soft text-personal'}`}>
            {r.suspected ? 'Suspected phishing' : 'Not suspected'} (score {str(r.score)}; 4 or more is suspected)
          </p>
          {((r.reasons as string[] | undefined) ?? []).length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5">
              {(r.reasons as string[]).map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {links.length > 0 && (
            <p>
              Links: {links.map((l) => l.host).join(', ')}
              {r.suspected ? ', all withheld from the agent.' : '.'}
            </p>
          )}
          <div>
            <p className="font-bold">What the agent reads</p>
            <p className="mt-1 whitespace-pre-wrap rounded-lg bg-quiet-soft p-3">{str(reads.body)}</p>
          </div>
        </div>
      )}
    </div>
  )
}

function LockTab({ onError }: { onError: OnError }) {
  const { info } = useMode()
  const lock = info?.examples.lock ?? null
  const [args, setArgs] = useState(pretty(lock?.hold.args ?? {}))
  const check = useCheck(onError)
  if (!lock) return <p className="text-muted">No held call in this recording to start from.</p>
  const submit = () => {
    try {
      check.run({ kind: 'lock', hold: lock.hold, edited_args: parse(args, 'The call') })
    } catch (error) {
      check.setProblem((error as Error).message)
    }
  }
  const r = check.result
  return (
    <div className="space-y-4">
      <p className="text-muted">
        An approval is locked to a fingerprint of the exact call: the agent, the tool, every argument
        and the hold id. Change one character below and check whether the approval would still run it.
      </p>
      <p>
        Held call: the {lock.hold.agent} agent's <span className="font-bold">{lock.hold.tool}</span>, approved with{' '}
        <Fingerprint value={lock.fingerprint} />.
      </p>
      <Field id="args" label="The call as it would run" value={args} onChange={setArgs} rows={8} mono />
      <Button kind="primary" onClick={submit} disabled={check.busy}>
        {check.busy ? 'Checking...' : 'Check the lock'}
      </Button>
      {check.problem && <p className="text-refused">{check.problem}</p>}
      {r && (
        <p className={`rounded-lg px-3 py-2 ${r.matches ? 'bg-personal-soft text-personal' : 'bg-refused-soft text-refused'}`}>
          <span className="font-bold">{str(r.outcome)}.</span> Approved {str(r.approved)}, this call {str(r.would_run)}.
        </p>
      )}
    </div>
  )
}

export function PlaygroundPage({ onError }: { onError: OnError }) {
  const { info } = useMode()
  const [tab, setTab] = useState<Tab>('reply')
  if (!info) return null
  return (
    <div className="space-y-4">
      <PageTitle
        title="Playground"
        lead="Steward's own checks, run on the server on what you type. Nothing is stored, no agent is involved and no model is called."
      />
      <div role="tablist" aria-label="What to check" className="flex flex-wrap gap-1">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`rounded-lg px-4 py-2 font-bold ${tab === id ? 'bg-ink text-card' : 'border border-line bg-card hover:bg-quiet-soft'}`}
          >
            {label}
          </button>
        ))}
      </div>
      <Panel>
        {tab === 'reply' && <ReplyTab examples={info.examples.reply} onError={onError} />}
        {tab === 'ask' && <AskTab examples={info.examples.ask} onError={onError} />}
        {tab === 'mail' && <MailTab example={info.examples.mail} onError={onError} />}
        {tab === 'lock' && <LockTab onError={onError} />}
      </Panel>
    </div>
  )
}
