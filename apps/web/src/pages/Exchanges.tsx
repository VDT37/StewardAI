// Shared and held: for each question between the agents, what the answering agent sent beside
// what Steward let cross, with the dropped parts struck through.
import { api, type Exchange } from '../api'
import { askPhrase, day, when, who } from '../format'
import { useLoad, type OnError } from '../hooks'
import { Actor, Empty, Loading, OutcomeTag, PageTitle, Panel } from '../ui'

type Payload = Record<string, unknown>

const load = () => api.exchanges()

function str(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

const BLOCK_CORE = new Set(['date', 'start', 'end', 'status', 'can_move'])
const LIMIT_CORE = new Set(['rule', 'value', 'applies_to'])

function Dropped({ name, value }: { name: string; value: unknown }) {
  return (
    <span className="mr-2 inline-block">
      <del className="text-trimmed">
        {name}: {str(value)}
      </del>
    </span>
  )
}

function blockText(b: Payload): string {
  const move = b.can_move ? 'can move' : "can't move"
  return `${day(str(b.date))}, ${str(b.start)} to ${str(b.end)}, ${str(b.status)}, ${move}`
}

function words(code: string): string {
  return code.replaceAll('_', ' ')
}

function Sent({ item }: { item: Exchange }) {
  const reply = (item.reply_in ?? {}) as Payload
  const extras = Object.entries(reply).filter(([k]) => !['blocks', 'limits', 'dietary', 'access'].includes(k))
  return (
    <div>
      <ul className="space-y-2">
        {Array.isArray(reply.blocks) &&
          (reply.blocks as Payload[]).map((b, i) => (
            <li key={i}>
              {blockText(b)}
              <div>
                {Object.entries(b)
                  .filter(([k]) => !BLOCK_CORE.has(k))
                  .map(([k, v]) => (
                    <Dropped key={k} name={k} value={v} />
                  ))}
              </div>
            </li>
          ))}
        {Array.isArray(reply.limits) &&
          (reply.limits as Payload[]).map((l, i) => (
            <li key={i}>
              {words(str(l.rule))} {str(l.value)} on {words(str(l.applies_to))}
              <div>
                {Object.entries(l)
                  .filter(([k]) => !LIMIT_CORE.has(k))
                  .map(([k, v]) => (
                    <Dropped key={k} name={k} value={v} />
                  ))}
              </div>
            </li>
          ))}
        {(['dietary', 'access'] as const).map((group) =>
          Array.isArray(reply[group])
            ? (reply[group] as unknown[]).map((need, i) => {
                const item = (typeof need === 'object' && need ? need : { need }) as Payload
                return (
                  <li key={`${group}-${i}`}>
                    {group}: {words(str(item.need))}
                    <div>
                      {Object.entries(item)
                        .filter(([k]) => k !== 'need')
                        .map(([k, v]) => (
                          <Dropped key={k} name={k} value={v} />
                        ))}
                    </div>
                  </li>
                )
              })
            : null,
        )}
      </ul>
      {extras.length > 0 && (
        <p className="mt-2">
          {extras.map(([k, v]) => (
            <Dropped key={k} name={k} value={v} />
          ))}
        </p>
      )}
    </div>
  )
}

function Crossed({ item }: { item: Exchange }) {
  const reply = (item.reply_out ?? {}) as Payload
  const widened = (item.rules_fired ?? []).includes('busy_widened')
  return (
    <div>
      <ul className="space-y-2">
        {Array.isArray(reply.blocks) &&
          (reply.blocks as Payload[]).map((b, i) => <li key={i}>{blockText(b)}</li>)}
        {Array.isArray(reply.limits) &&
          (reply.limits as Payload[]).map((l, i) => (
            <li key={i}>
              {words(str(l.rule))} {str(l.value)} on {words(str(l.applies_to))}
            </li>
          ))}
        {Array.isArray(reply.dietary) &&
          (reply.dietary as string[]).map((d) => <li key={d}>dietary: {words(d)}</li>)}
        {Array.isArray(reply.access) &&
          (reply.access as string[]).map((a) => <li key={a}>access: {words(a)}</li>)}
      </ul>
      {widened && (
        <p className="mt-2 text-sm text-muted">
          Times were widened outward to the {item.detail_minutes}-minute grid, so the exact
          start and end never cross.
        </p>
      )}
    </div>
  )
}

function ExchangeCard({ item }: { item: Exchange }) {
  const status =
    item.status === 'answered'
      ? (item.dropped_fields ?? []).length
        ? 'trimmed'
        : 'allowed'
      : item.status === 'held'
        ? 'held'
        : item.status === 'refused'
          ? 'refused'
          : 'note'
  if (item.status === 'notice') {
    const notices = (item.notices ?? []) as Payload[]
    return (
      <Panel>
        <p className="flex flex-wrap items-center gap-2">
          <span className="tabular-nums text-muted">{when(item.ts)}</span>
          <Actor name="Steward" />
          <span>passed on a change from the {str(item.contact)} side</span>
        </p>
        <ul className="mt-3 space-y-1">
          {notices.map((n, i) => (
            <li key={i} className="font-bold">
              {day(str(n.date))}, {str(n.start)} to {str(n.end)},{' '}
              {n.change === 'now_busy' ? 'now busy' : 'now free'}
              {n.change === 'now_busy' ? (n.can_move ? ', can move' : ", can't move") : ''}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-sm text-muted">
          The {who(item.asker).toLowerCase()} learns when you are busy, never why.
        </p>
      </Panel>
    )
  }
  if (item.status === 'stand-in issued' && item.stand_in) {
    return (
      <Panel>
        <p className="flex flex-wrap items-center gap-2">
          <span className="tabular-nums text-muted">{when(item.ts)}</span>
          <Actor name="Steward" />
          <span>
            gave the {who(item.asker).toLowerCase()} the stand-in{' '}
            <span className="font-mono">{item.stand_in.token}</span> for one taxi booking on{' '}
            {day(item.stand_in.trip_date)}. Your address goes only to the taxi company.
          </span>
        </p>
      </Panel>
    )
  }
  return (
    <Panel>
      <div className="flex flex-wrap items-center gap-2">
        <span className="tabular-nums text-muted">{when(item.ts)}</span>
        <Actor name={who(item.asker)} />
        <span>
          asked the {str(item.contact)} agent for {askPhrase(item.request_type, item.fields)}
        </span>
        <OutcomeTag outcome={status} rule={item.status === 'answered' ? undefined : item.rule} />
      </div>
      {item.status === 'answered' && (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-line p-4">
            <p className="mb-2 font-bold">What the {str(item.contact)} agent sent</p>
            <Sent item={item} />
          </div>
          <div className="rounded-lg border border-line p-4">
            <p className="mb-2 font-bold">What crossed to the {str(item.asker)} agent</p>
            <Crossed item={item} />
          </div>
        </div>
      )}
      {(item.status === 'refused' || item.status === 'held') && item.reason && (
        <p className="mt-2 text-muted">
          {item.reason.charAt(0).toUpperCase()}
          {item.reason.slice(1)}.
        </p>
      )}
      {item.status === 'no answer' && <p className="mt-2 text-muted">No answer came back, so nothing was shared.</p>}
      {(item.invalid_replies ?? []).length > 0 && (
        <p className="mt-2 text-sm text-muted">
          Steward turned away {item.invalid_replies?.length} reply that did not fit the request
          type: {item.invalid_replies?.[0]?.reason}
        </p>
      )}
    </Panel>
  )
}

export function ExchangesPage({ onError }: { onError: OnError }) {
  const [items] = useLoad(load, onError, 4000)
  return (
    <div>
      <PageTitle
        title="Shared and held"
        lead="Each question between the agents, with the full answer beside what crossed. Struck-through parts stayed with you."
      />
      {items === null ? (
        <Loading />
      ) : items.length === 0 ? (
        <Empty title="No questions between the agents yet">
          When the work agent asks the personal agent something, it appears here.
        </Empty>
      ) : (
        <div className="space-y-4">
          {items.map((item) => (
            <ExchangeCard key={item.id} item={item} />
          ))}
        </div>
      )}
    </div>
  )
}
