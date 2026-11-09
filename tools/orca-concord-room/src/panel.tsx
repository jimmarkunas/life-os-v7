// Orca native component panel: real Concord messages plus a composer to message live agents.
import { atom, Badge, Box, Button, Callout, Heading, read, Row, Select, set, Stack, Text, Textarea, plugin, type PanelContext } from 'orca'

type Message = { id: string; from: string; to: string; content: string; at: string; status: string; replyTo: string | null; receipt: string | null }
type Snapshot = { repoRoot: string; messages: Message[] }
type Agent = { id: string; state: string }
type Roster = { agents: Agent[]; me: string }

const snapshot = atom<Snapshot | null>(null)
const roster = atom<Roster>({ agents: [], me: '' })
const error = atom('')
const busy = atom(false)
const refreshAt = atom('')
const recipient = atom('')
const draft = atom('')
const sending = atom(false)
const sendNote = atom<{ tone: 'success' | 'danger'; text: string } | null>(null)
let fetching = false

async function refresh() {
  if (fetching) return
  fetching = true
  set(busy, true)
  try {
    const response = await plugin.request('concord.messages', {}, { timeoutMs: 12000 }) as Snapshot
    set(snapshot, response)
    set(error, '')
    set(refreshAt, new Date().toLocaleTimeString())
    try {
      const people = await plugin.request('concord.agents', {}, { timeoutMs: 12000 }) as Roster
      set(roster, people)
      const ids = people.agents.map(a => a.id)
      const chosen = read(recipient)
      if (!chosen || !ids.includes(chosen)) set(recipient, ids.find(id => id !== people.me && !id.startsWith('human:')) ?? '')
    } catch { /* roster is optional for reading */ }
  } catch (err: unknown) {
    set(snapshot, null)
    set(roster, { agents: [], me: '' })
    set(error, err instanceof Error ? err.message : String(err))
  } finally {
    fetching = false
    set(busy, false)
  }
}

async function send() {
  if (read(sending)) return
  const to = read(recipient)
  const content = read(draft)
  set(sending, true)
  set(sendNote, null)
  try {
    const out = await plugin.request('concord.send', { to, content }, { timeoutMs: 20000 }) as { messageId: string; status: string; delivery: string; outlook: string }
    set(draft, '')
    set(sendNote, { tone: 'success', text: `Sent to ${to} (${out.status}${out.delivery ? `, ${out.delivery}` : ''}). Receipts below are Concord's own; "queued" is not "delivered".` })
    void refresh()
  } catch (err: unknown) {
    set(sendNote, { tone: 'danger', text: err instanceof Error ? err.message : String(err) })
  } finally {
    set(sending, false)
  }
}

export function setup(ctx: PanelContext): void {
  void refresh()
  const interval = setInterval(() => { void refresh() }, 3000)
  ctx.subscriptions.push({ dispose: () => clearInterval(interval) })
}

const clock = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
const kindOf = (id: string) => id.split(':')[0]
const short = (id: string) => id.replace(/^(claude-code|codex|human):/, (m) => `${kindOf(m.slice(0, -1))} · `)

function Bubble({ msg, me }: { msg: Message; me: string }) {
  const mine = msg.from === me || kindOf(msg.from) === 'human'
  return (
    <Row justify={mine ? 'end' : 'start'}>
      <Box background={mine ? 'accent' : 'card'} border radius="lg" padding="sm" maxWidth="90%">
        <Stack gap="xs">
          <Row justify="between" gap="sm">
            <Text weight="semibold" size="sm">{short(msg.from)} → {short(msg.to)}</Text>
            <Badge tone={msg.status === 'failed' ? 'danger' : msg.status === 'replied' ? 'success' : 'neutral'}>{msg.receipt ?? msg.status}</Badge>
          </Row>
          <Text>{msg.content}</Text>
          <Text muted size="xs">{clock(msg.at)}{msg.replyTo ? ' · reply' : ''} · {msg.id.slice(0, 8)}</Text>
        </Stack>
      </Box>
    </Row>
  )
}

export default function Panel() {
  const current = read(snapshot)
  const people = read(roster)
  const messages = [...(current?.messages ?? [])].reverse()
  const targets = people.agents.filter(a => a.id !== people.me && !a.id.startsWith('human:'))
  const note = read(sendNote)
  const disabled = read(sending) || !read(recipient) || !read(draft).trim()
  return (
    <Stack gap="md">
      <Row justify="between">
        <Heading>Concord Room</Heading>
        <Badge tone={read(error) ? 'danger' : current ? 'success' : 'warning'}>
          {read(error) ? 'Unavailable' : current ? 'Live' : 'Connecting'}
        </Badge>
      </Row>
      {read(error) ? <Callout tone="danger" title="Cannot read Concord">{read(error)}</Callout> : null}

      <Row gap="xs" wrap>
        {targets.length === 0 && !read(error) ? <Text muted size="xs">No agents present yet.</Text> : null}
        {targets.map(a => <Badge key={a.id} tone={a.state.startsWith('live') ? 'success' : 'neutral'}>{short(a.id)} · {a.state}</Badge>)}
      </Row>

      <Stack gap="xs">
        <Select label="To" value={read(recipient)} onChange={(v) => set(recipient, v)} placeholder="Choose an agent"
          options={targets.map(a => ({ value: a.id, label: `${a.id} (${a.state})` }))} disabled={targets.length === 0} />
        <Textarea label="Message" labelHidden rows={3} maxLength={4000} value={read(draft)} onChange={(v) => set(draft, v)}
          placeholder={read(recipient) ? `Message ${read(recipient)} — ⌘↵ to send` : 'Choose an agent first'}
          onSubmit={() => { if (!disabled) void send() }} disabled={read(sending)} />
        <Row justify="between">
          <Text muted size="xs">Sending as {people.me || '…'}</Text>
          <Button icon="next" tone="accent" onPress={() => void send()} disabled={disabled}>{read(sending) ? 'Sending…' : 'Send'}</Button>
        </Row>
        {note ? <Callout tone={note.tone}>{note.text}</Callout> : null}
      </Stack>

      <Row justify="between">
        <Text muted size="xs">{current?.repoRoot ?? 'No verified repository'} · {messages.length} messages, newest first</Text>
        <Button variant="ghost" icon="refresh" onPress={() => void refresh()} disabled={read(busy)}>Refresh</Button>
      </Row>
      {messages.length === 0 && !read(error) ? <Text muted>No messages yet. Pick an agent above and say hello.</Text> : null}
      <Stack gap="sm">
        {messages.map(msg => <Bubble key={msg.id} msg={msg} me={people.me} />)}
      </Stack>
      <Text muted size="xs">Last refresh: {read(refreshAt) || 'Never'}. A readable local store is not evidence of remote delivery.</Text>
    </Stack>
  )
}
