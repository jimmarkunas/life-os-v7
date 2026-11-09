// Orca native component panel: real Concord messages, refreshed only while open.
import { atom, Badge, Button, Heading, read, Row, set, Stack, Text, plugin, type PanelContext } from 'orca'

type Message = { id: string; from: string; to: string; content: string; at: string; status: string; replyTo: string | null; receipt: string | null }
type Snapshot = { repoRoot: string; messages: Message[] }
const snapshot = atom<Snapshot | null>(null)
const error = atom('')
const busy = atom(false)
const refreshAt = atom('')
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
  } catch (err: unknown) {
    set(snapshot, null)
    set(error, err instanceof Error ? err.message : String(err))
  } finally {
    fetching = false
    set(busy, false)
  }
}

export function setup(ctx: PanelContext): void {
  void refresh()
  const interval = setInterval(() => { void refresh() }, 3000)
  ctx.subscriptions.push({ dispose: () => clearInterval(interval) })
}

export default function Panel() {
  const current = read(snapshot)
  const messages = current?.messages ?? []
  return (
    <Stack gap="md">
      <Row justify="between">
        <Heading>Concord Room</Heading>
        <Badge tone={read(error) ? 'danger' : current ? 'success' : 'warning'}>
          {read(error) ? 'Unavailable' : current ? 'Local feed' : 'Connecting'}
        </Badge>
      </Row>
      <Text muted size="xs">Live, read-only human view of actual Claude Code ↔ Codex messages. Send prompts from Orca's agent chats. No mock messages.</Text>
      <Row justify="between">
        <Text muted size="xs">{current?.repoRoot ?? 'No verified repository'} · {messages.length} recent messages</Text>
        <Button variant="ghost" icon="refresh" onPress={() => void refresh()} disabled={read(busy)}>Refresh</Button>
      </Row>
      {read(error) ? <Text tone="danger">{read(error)}</Text> : null}
      {messages.length === 0 && !read(error) ? <Text muted>No messages yet. Start Claude Code and Codex in the same Concord repository, confirm their adapters, and ask one to prompt the other.</Text> : null}
      <Stack gap="sm">
        {messages.map(msg => (
          <Stack gap="xs" key={msg.id}>
            <Row justify="between">
              <Text>{msg.from} → {msg.to}</Text>
              <Badge tone={msg.status === 'failed' ? 'danger' : msg.status === 'replied' ? 'success' : 'neutral'}>{msg.receipt ?? msg.status}</Badge>
            </Row>
            <Text>{msg.content}</Text>
            <Text muted size="xs">{msg.at} · {msg.id}{msg.replyTo ? ' · reply' : ''}</Text>
          </Stack>
        ))}
      </Stack>
      <Text muted size="xs">Last verified refresh: {read(refreshAt) || 'Never'}. A readable local store is not evidence of remote delivery.</Text>
    </Stack>
  )
}
