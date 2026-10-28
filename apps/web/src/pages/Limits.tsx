// What Steward does not do, and what this demo cannot show. Published on purpose.
import { PageTitle, Panel } from '../ui'

const LIMITS: [string, string][] = [
  [
    'Replay proves decisions, not agents',
    'A replay passing shows Steward decides the same way on the same actions. It does not show the agents would act the same way again: live runs differ from each other.',
  ],
  [
    'This page shows recordings',
    'Nothing a visitor does runs an agent or calls a model. Live mode, with the real agents and the Nebius key, stays behind the owner\'s SSH tunnel.',
  ],
  [
    'Steward only sees what goes through it',
    'OpenShell sandboxes send every agent connection to Steward. An agent given another channel outside those walls would be outside Steward\'s view.',
  ],
  [
    'The phishing screen is fixed rules',
    'It scores the signs a person is taught to look for. A message with none of them scores low. It never blocks reading; it withholds links and hidden text, and the owner keeps the original.',
  ],
  [
    'The watcher\'s limits are fixed thresholds',
    'A slow attack under them is left to the daily ask limit, the burst check and the brief. Comparisons with earlier evenings learn from little history, so they only report and never pause.',
  ],
  [
    'A model words the brief',
    'Its sentences can be awkward. Code checks every time, number and weekday against the facts and falls back to a template; the question you approve is always written by code.',
  ],
  [
    'Pinned models',
    'Each role uses an exact Token Factory model ID. When Token Factory retired the models first used here (removal on 9 Nov 2026), the switch was a config change plus a test run.',
  ],
  [
    'One server',
    'The demo and the owner\'s Steward run on one small VM. If it is down, this link is down. Rate limits live in memory and reset when the demo restarts.',
  ],
  [
    'Synthetic data',
    'The calendars, mail and profile are fixtures with planted private details, so a leak can be detected. The story is fixed to Sunday 11 to Thursday 15 October 2026.',
  ],
]

export function LimitsPage() {
  return (
    <div className="space-y-4">
      <PageTitle title="Limits" lead="What Steward does not do, and what this demo cannot show." />
      <Panel>
        <dl className="space-y-4">
          {LIMITS.map(([title, text]) => (
            <div key={title}>
              <dt className="font-bold">{title}</dt>
              <dd className="text-muted">{text}</dd>
            </div>
          ))}
        </dl>
      </Panel>
      <Panel title="Accessibility">
        <p className="text-muted">
          Atkinson Hyperlegible at 16 px, colour always paired with a text label, every control
          reachable from the keyboard with a visible focus ring, light and dark themes, and no motion
          when the system asks for reduced motion.
        </p>
      </Panel>
    </div>
  )
}
