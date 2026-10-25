// The policy file, read-only: who may use what, and which calls wait for the owner.
import { api, type PolicyView } from '../api'
import { who } from '../format'
import { useLoad, type OnError } from '../hooks'
import { Actor, Loading, PageTitle, Panel } from '../ui'

const load = () => api.policy()

const CLASS_WORD: Record<string, [string, string]> = {
  read: ['Reads', 'Allowed'],
  write: ['Writes', 'Allowed; held if the item changed since it was read'],
  spend: ['Spends money', 'Allowed within the trip limit, held above it'],
  send: ['Sends out', 'Always waits for you'],
  change_paid: ['Changes a paid booking', 'Always waits for you'],
  destructive: ['Deletes', 'Always waits for you'],
}

function usedBy(policy: PolicyView, toolId: string): string[] {
  return Object.entries(policy.agents)
    .filter(([, agent]) => agent.tools.includes(toolId))
    .map(([name]) => name)
}

export function PolicyPage({ onError }: { onError: OnError }) {
  const [policy] = useLoad(load, onError, 60000)
  if (!policy) return <Loading />
  const tools = Object.entries(policy.tools).filter(([id]) => usedBy(policy, id).length > 0)
  return (
    <div className="space-y-4">
      <PageTitle
        title="Policy"
        lead="Read from config/steward.yaml. Classes come from this file, never from what a tool says about itself."
      />
      <Panel title="Who may ask whom">
        {policy.contacts.map((c) => (
          <p key={`${c.from}-${c.to}`}>
            The {c.from} agent may ask the {c.to} agent for{' '}
            {c.request_types.map((t) => t.replaceAll('_', ' ')).join(', ')}, up to {c.daily_limit} times a
            day{c.burst_max ? ` and ${c.burst_max} in any ${c.burst_minutes} minutes` : ''}. Busy times
            are widened to {c.detail_minutes} minutes. Addresses always wait for you.
          </p>
        ))}
        <p className="mt-2 text-muted">Never shared: {policy.never_share.map((n) => n.replaceAll('_', ' ')).join(', ')}.</p>
      </Panel>
      <Panel title="Tools">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="text-muted">
              <tr>
                <th className="py-2 pr-4 font-normal">Tool</th>
                <th className="py-2 pr-4 font-normal">Used by</th>
                <th className="py-2 pr-4 font-normal">Class</th>
                <th className="py-2 pr-4 font-normal">What Steward does</th>
              </tr>
            </thead>
            <tbody>
              {tools.map(([id, rule]) => {
                const [word, action] = CLASS_WORD[rule.class] ?? [rule.class, '']
                const egress = rule.egress ?? ['send', 'spend', 'change_paid'].includes(rule.class)
                const notes = [
                  rule.private && 'returns private data',
                  (rule.untrusted || rule.trust_field) && 'can bring in untrusted content',
                  egress && 'can send data out',
                  rule.stand_in_args.length > 0 && 'address only as a stand-in',
                ].filter(Boolean)
                return (
                  <tr key={id} className="border-t border-line align-top">
                    <td className="py-2 pr-4">{id}</td>
                    <td className="py-2 pr-4">
                      <span className="flex flex-wrap gap-1">
                        {usedBy(policy, id).map((agent) => (
                          <Actor key={agent} name={who(agent)} />
                        ))}
                      </span>
                    </td>
                    <td className="py-2 pr-4">{word}</td>
                    <td className="py-2 pr-4">
                      {action}
                      {notes.length > 0 && <span className="block text-muted">{notes.join('; ')}</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Trust and holds">
        <p>
          Mail from {policy.trusted_mail_domains.join(' and ')} counts as trusted; any other sender
          marks the agent as having seen untrusted content.
        </p>
        <p className="mt-2">
          A held call waits {policy.holds.expires_hours} hours. Approval links last{' '}
          {policy.holds.link_minutes} minutes, and an address stand-in {policy.holds.stand_in_hours} hours.
        </p>
      </Panel>
    </div>
  )
}
