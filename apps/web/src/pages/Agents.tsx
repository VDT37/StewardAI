// The agents: their passes, spend against budget, trifecta flags and tools.
import { useCallback } from 'react'
import { api, type AgentDetail, type AgentSummary, type Flags } from '../api'
import { who } from '../format'
import { useLoad, type OnError } from '../hooks'
import { Actor, Button, EventLine, Loading, PageTitle, Panel, Spend } from '../ui'

const loadAgents = () => api.agents()

const CLASS_WORD: Record<string, string> = {
  read: 'Reads',
  write: 'Writes',
  spend: 'Spends money',
  send: 'Sends out',
  change_paid: 'Changes a paid booking',
  destructive: 'Deletes',
}

function FlagState({ flags }: { flags: Flags }) {
  const items: [string, string[]][] = [
    ['Has read your private data', flags.private_read],
    ['Has seen untrusted content', flags.untrusted_seen],
  ]
  const both = flags.private_read.length > 0 && flags.untrusted_seen.length > 0
  return (
    <div>
      <ul className="space-y-1">
        {items.map(([label, sources]) => (
          <li key={label} className="flex items-start gap-2">
            <span
              aria-hidden
              className={`mt-1.5 inline-block h-2.5 w-2.5 shrink-0 rounded-full ${sources.length ? 'bg-held' : 'bg-line'}`}
            />
            <span>
              {label}: {sources.length ? <span className="text-muted">{sources.join(', ')}</span> : 'no'}
            </span>
          </li>
        ))}
      </ul>
      {both && (
        <p className="mt-2 rounded-lg bg-held-soft px-3 py-2 text-held">
          Both are true, so every call that can send data out waits for you until 04:00.
        </p>
      )}
    </div>
  )
}

export function AgentsPage({ onError }: { onError: OnError }) {
  const [agents] = useLoad(loadAgents, onError)
  if (!agents) return <Loading />
  return (
    <div>
      <PageTitle title="Agents" lead="Each agent reaches models, tools and the other agent only through Steward, with its own pass." />
      <div className="grid gap-4 lg:grid-cols-2">
        {agents.map((agent: AgentSummary) => (
          <a key={agent.id} href={`#/agents/${agent.id}`} className="block rounded-xl focus-visible:outline-offset-4">
            <Panel className="h-full hover:border-muted">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Actor name={who(agent.id)} />
                <span className={agent.pass === 'active' ? 'text-personal' : 'font-bold text-refused'}>
                  {agent.pass === 'active' ? 'Pass active' : 'Pass suspended'}
                </span>
              </div>
              <p className="mt-2 text-muted">{agent.model.split('/').pop()}</p>
              <div className="mt-4">
                <Spend spent={agent.spend_today_usd} budget={agent.budget_usd_per_day} />
              </div>
              <div className="mt-4">
                <FlagState flags={agent.flags} />
              </div>
              <p className="mt-4">
                {agent.holds_pending
                  ? `${agent.holds_pending} call${agent.holds_pending === 1 ? '' : 's'} waiting for you`
                  : 'Nothing waiting for you'}
                {Object.entries(agent.asks_today).map(([to, n]) => `; ${n} asks to ${to} today`)}
              </p>
            </Panel>
          </a>
        ))}
      </div>
    </div>
  )
}

export function AgentPage({ id, onError }: { id: string; onError: OnError }) {
  const load = useCallback(() => api.agent(id), [id])
  const [agent, reload] = useLoad<AgentDetail>(load, onError)
  if (!agent) return <Loading />
  const toggle = () => (agent.pass === 'active' ? api.suspend(id) : api.resume(id)).then(reload, onError)
  const clear = () => api.clearFlags(id).then(reload, onError)
  return (
    <div className="space-y-4">
      <PageTitle title={who(agent.id)} lead={agent.model} />
      <Panel
        title="Pass"
        aside={
          <Button kind={agent.pass === 'active' ? 'danger' : 'primary'} onClick={toggle}>
            {agent.pass === 'active' ? 'Suspend pass' : 'Resume pass'}
          </Button>
        }
      >
        <p>
          {agent.pass === 'active'
            ? 'Active. Suspending it stops every model, tool and agent call at once; the Nebius key is not touched.'
            : 'Suspended. Every call from this agent is turned away until you resume it.'}
        </p>
        <div className="mt-4 max-w-xl">
          <Spend spent={agent.spend_today_usd} budget={agent.budget_usd_per_day} />
        </div>
        {agent.trip_limit_gbp && (
          <p className="mt-3">Books travel without asking up to £{agent.trip_limit_gbp} a trip.</p>
        )}
      </Panel>
      <Panel
        title="Trifecta flags"
        aside={
          <Button onClick={clear} disabled={!agent.flags.private_read.length && !agent.flags.untrusted_seen.length}>
            Clear flags
          </Button>
        }
      >
        <FlagState flags={agent.flags} />
      </Panel>
      <Panel title="Tools on the pass">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="text-muted">
              <tr>
                <th className="py-2 pr-4 font-normal">The agent sees</th>
                <th className="py-2 pr-4 font-normal">What it does</th>
                <th className="py-2 pr-4 font-normal">Pin</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {agent.tool_list.map((tool) => (
                <tr key={tool.name} className="border-t border-line">
                  <td className="py-2 pr-4">{tool.name}</td>
                  <td className="py-2 pr-4">
                    {tool.state === 'built-in'
                      ? 'Steward itself'
                      : `${CLASS_WORD[tool.tool_class ?? ''] ?? tool.tool_class}${tool.egress ? ', can send data out' : ''}`}
                  </td>
                  <td className="py-2 pr-4">
                    {tool.state === 'mismatch' ? (
                      <span className="font-bold text-refused">Changed, switched off</span>
                    ) : tool.state === 'built-in' ? (
                      ''
                    ) : (
                      <span className="text-muted">Pinned</span>
                    )}
                  </td>
                  <td className="py-2 text-right">
                    {tool.state === 'mismatch' && (
                      <Button onClick={() => api.repin(tool.tool_id).then(reload, onError)}>Accept the change</Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {agent.routes.length > 0 && (
        <Panel title="May ask">
          {agent.routes.map((route) => (
            <p key={route.to}>
              The {route.to} agent, about {route.request_types.map((t) => t.replaceAll('_', ' ')).join(', ')}.
              Busy times are widened to {route.detail_minutes} minutes. Up to {route.daily_limit} asks a day
              {route.burst_max ? ` and ${route.burst_max} in any ${route.burst_minutes} minutes` : ''};{' '}
              {agent.asks_today[route.to] ?? 0} used today.
            </p>
          ))}
        </Panel>
      )}
      <Panel title="Recent activity">
        <ul>
          {agent.recent.map((event) => (
            <EventLine key={event.seq} event={event} />
          ))}
        </ul>
      </Panel>
    </div>
  )
}
