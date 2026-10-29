// Today: the morning brief Steward wrote on its own at 07:30, its one question, the watcher's
// state per agent with the contain buttons, and the mail Steward screened as phishing.
import { useState } from 'react'
import { api, ApiError, type Brief, type MailItem, type Status, type WatcherState } from '../api'
import { modelName, ruleLabel, when, who } from '../format'
import { useLoad, type OnError } from '../hooks'
import { useMode } from '../mode'
import { Actor, Button, Empty, Fingerprint, Loading, ModelsStrip, OwnerOnly, PageTitle, Panel } from '../ui'

const loadBrief = () => api.brief()
const loadWatcher = () => api.watcher()
const loadMail = () => api.mail()
const loadStatus = () => api.status()

function source(brief: Brief, scripted: boolean): string {
  if (brief.source === 'model') {
    return `Written by ${modelName(brief.model)} on Nebius Token Factory from facts picked in code, then checked in code.`
  }
  if (scripted) return 'Written from the template: the scripted evening has no model behind it.'
  const why = brief.problems?.filter(Boolean).join('; ') || 'no model'
  return `Written from the template, because the model's text was not used (${why}).`
}

const STATE_WORD: Record<string, string> = {
  pending: 'waiting',
  done: 'approved and done once',
  denied: 'declined',
  expired: 'expired',
  failed: 'approved, but it failed',
  unknown: 'approved, outcome unknown',
}

function Question({ brief, onDone, onError }: { brief: Brief; onDone: () => void; onError: OnError }) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const question = brief.question
  if (!question) return <p className="mt-4 font-bold">No question this morning.</p>
  const open = question.holds.some((h) => h.state === 'pending')
  const more = Number(brief.facts.pending ?? 0) - question.holds.length
  const answer = (choice: 'approve' | 'deny') => {
    setBusy(true)
    setProblem(null)
    api.answerBrief(brief.brief_id, choice).then(
      (result) => {
        setBusy(false)
        const failed = result.outcomes.find((o) => o.error)
        if (failed) setProblem(failed.error ?? null)
        onDone()
      },
      (error: unknown) => {
        setBusy(false)
        if (error instanceof ApiError) setProblem(error.message)
        else onError(error)
      },
    )
  }
  return (
    <div className="mt-5 grid overflow-visible rounded-xl border border-held-line lg:grid-cols-[1fr_16rem]">
      <div className="p-5">
        <p className="text-sm font-bold text-held">One question, written by code</p>
        <p className="mt-2 text-xl font-bold leading-snug">{question.text}</p>
        <ul className="mt-3 space-y-1 text-sm">
          {question.holds.map((h) => (
            <li key={h.hold_id}>
              {h.tool.replaceAll('_', ' ')}, held call {h.hold_id}: {STATE_WORD[h.state] ?? h.state}
            </li>
          ))}
        </ul>
        {more > 0 && (
          <p className="mt-2 text-sm text-muted">
            {more} more call{more === 1 ? '' : 's'} waiting on the approvals page.
          </p>
        )}
      </div>
      <div className="pass-stub flex flex-col justify-between gap-4 bg-held-soft p-5">
        <div>
          <p className="text-muted">Locked to</p>
          {question.holds.map((h) => (
            <p key={h.hold_id}>
              <Fingerprint value={h.fingerprint} />
            </p>
          ))}
        </div>
        {open ? (
          <div className="flex flex-col gap-2">
            <Button kind="primary" owner disabled={busy} onClick={() => answer('approve')}>
              Approve
            </Button>
            <Button kind="danger" owner disabled={busy} onClick={() => answer('deny')}>
              Deny
            </Button>
            <OwnerOnly />
            {problem && <p className="text-sm text-refused">{problem}</p>}
          </div>
        ) : (
          <p className="font-bold">Answered.</p>
        )}
      </div>
    </div>
  )
}

function BriefPanel({ onError }: { onError: OnError }) {
  const { demo, recording } = useMode()
  const [brief, reload] = useLoad<Brief>(loadBrief, onError, 5000)
  const [running, setRunning] = useState(false)
  if (!brief) return <Loading />
  const runNow = () => {
    setRunning(true)
    api
      .runBrief()
      .then(reload, onError)
      .finally(() => setRunning(false))
  }
  const now = !demo && (
    <Button onClick={runNow} disabled={running}>
      {running ? 'Writing...' : 'Write a brief now'}
    </Button>
  )
  if (brief.state === 'none') {
    return (
      <Panel title="Morning brief" aside={now}>
        <Empty title="No brief yet">
          Steward writes one on its own at 07:30 London time, on its own clock (story time while the
          Manchester evening plays).
        </Empty>
      </Panel>
    )
  }
  const lines = (brief.summary || '').split('\n').filter((l) => l.trim())
  return (
    <Panel title={`Morning brief, ${when(brief.ts)}`} aside={now}>
      <p className="text-sm text-muted">{source(brief, recording?.kind === 'scripted')}</p>
      <ul className="mt-4 space-y-2">
        {lines.map((line, i) => {
          const [topic, ...rest] = line.split(':')
          return (
            <li key={i}>
              {rest.length ? (
                <>
                  <span className="font-bold">{topic}:</span>
                  {rest.join(':')}
                </>
              ) : (
                line
              )}
            </li>
          )
        })}
      </ul>
      <Question brief={brief} onDone={reload} onError={onError} />
    </Panel>
  )
}

const PASS_STYLE: Record<string, [string, string]> = {
  active: ['bg-personal-soft text-personal', 'Active'],
  paused: ['bg-held-soft text-held', 'Paused'],
  revoked: ['bg-refused-soft text-refused', 'Revoked'],
}

function WatcherPanel({ onError }: { onError: OnError }) {
  const [state, reload] = useLoad<WatcherState>(loadWatcher, onError, 5000)
  if (!state) return null
  const set = (agent: string, pause: boolean) =>
    (pause ? api.suspend(agent) : api.resume(agent)).then(reload, onError)
  const unusual = state.baselines.filter((b) => b.verdict === 'unusual')
  return (
    <Panel title="Watcher">
      <p className="max-w-3xl text-muted">
        Hard limits in code pause an agent at once: the same call {state.limits?.same_call_max ?? 10} times
        in {state.limits?.same_call_minutes ?? 5} minutes, {state.limits?.refusals_max ?? 8} refused calls in{' '}
        {state.limits?.refusals_minutes ?? 10} minutes, or over {state.limits?.model_calls_per_minute ?? 60} model
        calls a minute. Comparisons with earlier evenings only report.
      </p>
      <ul className="mt-4 space-y-3">
        {Object.entries(state.agents).map(([agent, a]) => {
          const [style, word] = PASS_STYLE[a.pass] ?? PASS_STYLE.active
          const pause = state.pauses.find((p) => p.agent === agent)
          return (
            <li key={agent} className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3 last:border-0">
              <div className="flex flex-wrap items-center gap-2">
                <Actor name={who(agent)} />
                <span className={`rounded-md px-2 py-0.5 text-sm font-bold ${style}`}>{word}</span>
                {a.tools_off.length > 0 && (
                  <span className="text-sm text-muted">Turned off: {a.tools_off.join(', ')}</span>
                )}
                {pause && (
                  <span className="text-sm text-muted">
                    Last watcher pause {when(pause.ts)}: {ruleLabel(pause.rule)}
                  </span>
                )}
              </div>
              <div className="flex gap-2">
                {a.pass === 'active' ? (
                  <Button kind="danger" owner onClick={() => set(agent, true)}>
                    Pause
                  </Button>
                ) : (
                  <Button kind="primary" owner disabled={a.pass === 'revoked'} onClick={() => set(agent, false)}>
                    Resume
                  </Button>
                )}
                <a href={`#/agents/${agent}`} className="rounded-lg border border-line px-4 py-2 font-bold hover:bg-quiet-soft">
                  Tools
                </a>
              </div>
            </li>
          )
        })}
      </ul>
      <p className="mt-3 text-sm text-muted">
        {state.baselines.length === 0
          ? 'No earlier evenings to compare with yet.'
          : unusual.length
            ? `Unusual against earlier evenings: ${unusual.map((b) => `${b.agent} ${b.metric.replaceAll('_', ' ')} (${b.value})`).join(', ')}.`
            : 'Nothing unusual against earlier evenings.'}
      </p>
      <OwnerOnly />
    </Panel>
  )
}

function MailPanel({ onError }: { onError: OnError }) {
  const [mail, reload] = useLoad<MailItem[]>(loadMail, onError, 8000)
  if (!mail || mail.length === 0) return null
  const verdict = (id: string, value: 'phishing' | 'not_phishing') =>
    api.mailVerdict(id, value).then(reload, onError)
  return (
    <Panel title="Suspected phishing">
      <p className="max-w-3xl text-muted">
        Steward scores every email an agent reads against the signs a person is taught to look for.
        For a suspected one it withholds every link and drops hidden text, so the agent cannot open
        what it never sees.
      </p>
      <ul className="mt-4 space-y-4">
        {mail.map((m) => (
          <li key={m.message_id} className="rounded-lg border border-line p-4">
            <p className="font-bold">
              "{m.subject}" from {m.sender_domain}
            </p>
            <p className="text-sm text-muted">
              First read by the {m.agent} agent {when(m.first_seen)}. {m.links.length} link
              {m.links.length === 1 ? '' : 's'} withheld
              {m.hidden_text.length ? ', hidden text removed' : ''}.
              {m.reported_by_agent ? ' The agent reported it.' : ''}
            </p>
            <ul className="mt-2 list-disc space-y-0.5 pl-5">
              {m.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {m.verdict ? (
                <span className="font-bold">
                  You marked it {m.verdict === 'phishing' ? 'as phishing' : 'as not phishing'}.
                </span>
              ) : (
                <>
                  <Button kind="primary" owner onClick={() => verdict(m.message_id, 'phishing')}>
                    It is phishing
                  </Button>
                  <Button owner onClick={() => verdict(m.message_id, 'not_phishing')}>
                    Not phishing, release its links
                  </Button>
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function OwnerModels({ onError }: { onError: OnError }) {
  const [status] = useLoad<Status>(loadStatus, onError, 30000)
  if (!status) return null
  const models = status.models ?? {}
  return (
    <ModelsStrip
      models={[
        ['Work agent', modelName(models.work)],
        ['Personal agent', modelName(models.personal)],
        ['Morning brief', modelName(models.brief)],
        ['Every decision', 'code, no model'],
      ]}
    />
  )
}

export function TodayPage({ onError }: { onError: OnError }) {
  const { demo } = useMode()
  return (
    <div className="space-y-4">
      <PageTitle
        title={demo ? 'Morning brief' : 'Today'}
        lead="Nobody asks for the brief: Steward writes it at 07:30 from what happened overnight. Code picks every fact and writes the one question; a model only words the summary."
      />
      {!demo && <OwnerModels onError={onError} />}
      <BriefPanel onError={onError} />
      <WatcherPanel onError={onError} />
      <MailPanel onError={onError} />
    </div>
  )
}
