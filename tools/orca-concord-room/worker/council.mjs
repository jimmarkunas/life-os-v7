// The Concord Room council: Orca's own orchestration used as the harness (what Buzz's buzz-acp does for Buzz).
//
// The room's worker is the COORDINATOR. It owns a Run and a silent "coordinator terminal" (Orca insists a coordinator is a terminal and types its
// "you have an orchestration message" line there, so it must be a terminal nobody else uses, never one of Jim's agent terminals). Each council
// member is a kept-open Claude or Codex worker (a chat) started with `orchestration worker-start`. A question goes out with `orchestration send`
// (Orca wakes even a finished chat), the answer comes back as a `worker_done` in the Run's mailbox, and `orchestration inbox` is read to show it.
//
// Everything that talks to Orca goes through ctx.orca(args, ms), so this file is tested without Orca. ctx = { orca, now, fail, stateFile, repoRoot }.
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import path from 'node:path'

export const COORDINATOR_TITLE = 'Concord Room council (leave alone)'
const MAX_LOG = 600

/** What a council member is told when it is started. It answers read-only, one worker_done per question, and reports its Concord id once. */
export const BRIEF = [
  "You are a council member in Jim's Concord Room. Jim will ask you questions as follow-up messages.",
  'Rules: answer each question concisely and honestly and say when you are unsure; you may read files in this repository to answer but must not edit, create or delete anything or run any command that changes anything; do not start other work.',
  'Report each answer with worker_done, putting your answer in the result text.',
  'For THIS first task: finish with worker_done whose result text is exactly READY followed by a space and your Concord agent id (the claude-code:xxxxxxxx or codex:xxxxxxxx id your session start hook printed), or READY none if you did not see one.'
].join(' ')

const emptyState = (repoRoot) => ({ version: 1, repoRoot, coordinator: '', run: '', members: [], log: [], seen: [] })

export async function loadState(file, repoRoot) {
  try {
    const s = JSON.parse(await readFile(file, 'utf8'))
    if (s && s.version === 1 && Array.isArray(s.members) && Array.isArray(s.log) && Array.isArray(s.seen)) return s.repoRoot === repoRoot ? s : emptyState(repoRoot)
  } catch { /* first use, or unreadable: start empty */ }
  return emptyState(repoRoot)
}

export async function saveState(file, state) {
  await mkdir(path.dirname(file), { recursive: true })
  const trimmed = { ...state, log: state.log.slice(-MAX_LOG), seen: state.seen.slice(-2000) }
  const tmp = `${file}.${process.pid}.tmp`
  await writeFile(tmp, JSON.stringify(trimmed, null, 1), { mode: 0o600 })
  await rename(tmp, file)
}

/** Orca wraps results as { ok, result }; unwrap and fail clearly on anything else. */
const result = (out) => (out && typeof out === 'object' && 'result' in out ? out.result : out)

export const memberIdFor = (agent, dispatchId) => `council-${agent}:${String(dispatchId).replace(/^ctx_/u, '').slice(0, 8)}`
const active = (state) => state.members.filter((m) => !m.released)

/** The silent terminal Orca addresses its coordinator messages to. Reused while it is open; recreated (and the old Run abandoned) if it is gone. */
export async function ensureCoordinator(ctx, state) {
  if (state.coordinator) {
    const list = result(await ctx.orca(['terminal', 'list', '--json'], 20000))
    const terminals = Array.isArray(list?.terminals) ? list.terminals : []
    if (terminals.some((t) => t.handle === state.coordinator && t.connected !== false)) return state.coordinator
    for (const m of state.members) m.released = true                    // their Run's coordinator is gone; they are no longer reachable through it
    state.run = ''; state.coordinator = ''
  }
  const made = result(await ctx.orca(['terminal', 'create', '--worktree', `path:${ctx.repoRoot}`, '--title', COORDINATOR_TITLE, '--command', 'stty -echo; tail -f /dev/null', '--json'], 30000))
  const handle = made?.terminal?.handle ?? made?.handle
  if (typeof handle !== 'string' || !handle) ctx.fail('invalid_output', 'Orca did not return the council coordinator terminal.')
  state.coordinator = handle
  return handle
}

export async function ensureRun(ctx, state) {
  const coordinator = await ensureCoordinator(ctx, state)
  if (!state.run) {
    const made = result(await ctx.orca(['orchestration', 'run-create', '--from', coordinator, '--objective', 'Concord Room council', '--json'], 30000))
    if (typeof made?.runId !== 'string') ctx.fail('invalid_output', 'Orca did not create the council Run.')
    state.run = made.runId
  }
  return { coordinator, run: state.run }
}

/** Start one kept-open council member (a chat). It reports READY plus its Concord id when it is up, which sync() reads. */
export async function startMember(ctx, { agent }) {
  if (!['claude', 'codex'].includes(agent)) ctx.fail('invalid_params', 'Choose Claude or Codex.')
  const state = await loadState(ctx.stateFile, ctx.repoRoot)
  const { coordinator, run } = await ensureRun(ctx, state)
  const started = result(await ctx.orca(['orchestration', 'worker-start', '--from', coordinator, '--run', run, '--worktree', `path:${ctx.repoRoot}`, '--agent', agent, '--keep-open', '--task-title', `Council member (${agent})`, '--spec', BRIEF, '--json'], 90000))
  if (typeof started?.dispatchId !== 'string' || typeof started?.terminal !== 'string') ctx.fail('invalid_output', 'Orca did not start the council member.')
  const member = { id: memberIdFor(agent, started.dispatchId), dispatchId: started.dispatchId, taskId: started.taskId ?? null, agent, worker: started.terminal, concordId: null, joined: false, released: false, startedAt: ctx.now() }
  state.members.push(member)
  await saveState(ctx.stateFile, state)
  return { id: member.id, agent }
}

/** One send per member. The task id Orca returns is what the answer will carry, so it joins question to answer. */
export async function askMembers(ctx, { ids, text, from }) {
  const body = String(text ?? '').trim()
  if (!body) ctx.fail('invalid_params', 'Type a message first.')
  const state = await loadState(ctx.stateFile, ctx.repoRoot)
  if (!state.coordinator || !state.run) ctx.fail('conflict', 'There is no council yet. Start a member first.')
  const framed = `From ${String(from).replace(/^human:/u, '')}: ${body}\n\n(Council question. Answer concisely and honestly. Read-only: do not edit anything. Report your answer with worker_done; the result text is your answer.)`
  const sent = []
  for (const id of ids) {
    const m = active(state).find((x) => x.id === id)
    if (!m) ctx.fail('invalid_params', `${id} is not a council member.`)
    const out = result(await ctx.orca(['orchestration', 'send', '--from', state.coordinator, '--run', state.run, '--to', `dispatch:${m.dispatchId}`, '--subject', 'Room question', '--body', framed, '--json'], 30000))
    if (typeof out?.taskId !== 'string') ctx.fail('invalid_output', 'Orca did not confirm the question.')
    state.log.push({ k: 'q', id: out.taskId, to: m.id, from, text: body, at: ctx.now() })
    sent.push({ id: m.id, taskId: out.taskId, delivery: String(out.delivery ?? '') })
  }
  await saveState(ctx.stateFile, state)
  return sent
}

/** The row's payload as an object (Orca stores it as a JSON string; tolerate an object). */
function payloadOf(msg) {
  const p = msg?.payload
  if (p && typeof p === 'object') return p
  try { const o = JSON.parse(p); return o && typeof o === 'object' ? o : {} } catch { return {} }
}

const CONCORD_ID = /\b(?:claude-code|codex):[a-z0-9]+\b/u

/** Pull the answer a member wrote in its chat when it ended its turn without worker_done (Orca then sends an "escalation"). */
async function recoverAnswer(ctx, member) {
  try {
    const read = result(await ctx.orca(['orchestration', 'worker-read', '--dispatch', member.dispatchId, '--json'], 30000))
    const messages = read?.transcript?.messages ?? []
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i]
      if (m?.role !== 'assistant') continue
      const text = (Array.isArray(m.blocks) ? m.blocks : []).filter((b) => b?.type === 'text').map((b) => b.text).join('\n').trim()
      if (text) return { text, at: Number.isFinite(m.timestamp) ? new Date(m.timestamp).toISOString() : ctx.now() }
    }
  } catch { /* fall through: nothing recovered */ }
  return null
}

/** Read the Run's mailbox and append anything new to the log. Cheap when idle: one `inbox` call. Returns true if something changed. */
export async function syncCouncil(ctx) {
  const state = await loadState(ctx.stateFile, ctx.repoRoot)
  if (!state.run || active(state).length === 0) return false
  const inbox = result(await ctx.orca(['orchestration', 'inbox', '--terminal', `run:${state.run}`, '--limit', '200', '--full', '--json'], 30000))
  const messages = Array.isArray(inbox?.messages) ? [...inbox.messages].reverse() : []        // Orca lists newest first
  let changed = false
  for (const msg of messages) {
    if (!msg || typeof msg.id !== 'string' || state.seen.includes(msg.id)) continue
    state.seen.push(msg.id); changed = true
    const sender = msg.from_handle ?? msg.from                  // Orca's inbox rows carry from_handle; payload is a JSON string
    const member = state.members.find((m) => m.worker === sender)
    if (!member) continue
    const body = String(msg.body ?? '').trim()
    const taskId = payloadOf(msg).taskId ?? null
    if (msg.type === 'worker_done') {
      if (!member.joined && /^READY\b/u.test(body)) {
        member.joined = true; member.concordId = (CONCORD_ID.exec(body) || [null])[0]
        state.log.push({ k: 'join', id: msg.id, from: member.id, at: ctx.now() })
      } else state.log.push({ k: 'a', id: msg.id, taskId, from: member.id, text: body, at: ctx.now() })
    } else if (msg.type === 'escalation') {
      const got = await recoverAnswer(ctx, member)
      state.log.push({ k: 'a', id: msg.id, taskId, from: member.id, text: got ? got.text : '(It ended its turn without an answer. Ask again.)', at: got ? got.at : ctx.now(), note: got ? 'answered in its chat without reporting' : 'no answer' })
    } else if (msg.type === 'question') {
      state.log.push({ k: 'a', id: msg.id, taskId: null, from: member.id, text: body, at: ctx.now(), note: 'asks you' })
    }
  }
  if (changed) await saveState(ctx.stateFile, state)
  return changed
}

/** Hand a member back to Jim (Orca keeps its chat open as an ordinary chat) and drop it from the council. */
export async function releaseMember(ctx, id) {
  const state = await loadState(ctx.stateFile, ctx.repoRoot)
  const m = active(state).find((x) => x.id === id)
  if (!m) ctx.fail('invalid_params', `${id} is not a council member.`)
  await ctx.orca(['orchestration', 'worker-release', '--dispatch', m.dispatchId, '--json'], 30000)
  m.released = true
  await saveState(ctx.stateFile, state)
  return { id }
}

/** Members as room agents. Not yet joined = "idle" until its READY report arrives. */
export function councilAgents(state) {
  return active(state).map((m) => ({ id: m.id, state: m.joined ? 'live/active' : 'idle/active', summary: `Council member (${m.agent})${m.joined ? '' : ', starting'}`, council: true }))
}

/** Concord ids the council members registered under: the room shows the council member, not a second Concord agent. */
export const councilConcordIds = (state) => state.members.filter((m) => m.concordId).map((m) => m.concordId)

/** The council's questions and answers in the same shape as Concord messages, so the room renders them with no special case. */
export function councilMessages(state) {
  const askers = new Map(state.log.filter((e) => e.k === 'q').map((e) => [e.id, e.from]))
  return state.log.flatMap((e) => {
    if (e.k === 'q') return [{ id: `council-q-${e.id}`, from: e.from, to: e.to, content: e.text, at: e.at, status: 'sent', replyTo: null, receipt: null }]
    if (e.k === 'a') return [{ id: `council-a-${e.id}`, from: e.from, to: askers.get(e.taskId) ?? 'human:me', content: e.note ? `${e.text}\n(${e.note})` : e.text, at: e.at, status: 'replied', replyTo: e.taskId ? `council-q-${e.taskId}` : null, receipt: null }]
    return []
  })
}
