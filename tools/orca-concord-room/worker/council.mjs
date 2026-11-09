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

const emptyState = (repoRoot) => ({ version: 1, repoRoot, coordinator: '', run: '', members: [], log: [], seen: [], rounds: [], snaps: {} })

/** The worker is one process: every read-modify-write of council.json goes through this queue so a sync and a send never overwrite each other. */
let chain = Promise.resolve()
const locked = (fn) => { const run = chain.then(fn, fn); chain = run.catch(() => {}); return run }

export async function loadState(file, repoRoot) {
  try {
    const s = JSON.parse(await readFile(file, 'utf8'))
    if (s && s.version === 1 && Array.isArray(s.members) && Array.isArray(s.log) && Array.isArray(s.seen)) return s.repoRoot === repoRoot ? { ...s, rounds: Array.isArray(s.rounds) ? s.rounds : [], snaps: s.snaps && typeof s.snaps === 'object' ? s.snaps : {} } : emptyState(repoRoot)
  } catch { /* first use, or unreadable: start empty */ }
  return emptyState(repoRoot)
}

export async function saveState(file, state) {
  await mkdir(path.dirname(file), { recursive: true })
  const trimmed = { ...state, log: state.log.slice(-MAX_LOG), seen: state.seen.slice(-2000), rounds: (state.rounds ?? []).slice(-20), snaps: Object.fromEntries(Object.entries(state.snaps ?? {}).slice(-100)) }
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
export const startMember = (ctx, input) => locked(() => startMemberLocked(ctx, input))
async function startMemberLocked(ctx, { agent }) {
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

/** One orchestration send to one member; the task id Orca returns is what its answer will carry, so it joins question to answer. */
async function sendOne(ctx, state, m, framed, logText, from) {
  const before = await snapshotOf(ctx)
  const out = result(await ctx.orca(['orchestration', 'send', '--from', state.coordinator, '--run', state.run, '--to', `dispatch:${m.dispatchId}`, '--subject', 'Room question', '--body', framed, '--json'], 30000))
  // A member that is mid-task (or waiting for your answer to its own question) gets the message into that task: Orca returns a message id and no
  // new task id. It was delivered, so say so, and let the member's answer (which carries the old task id) match it by member instead.
  const queued = typeof out?.taskId !== 'string'
  if (queued && typeof out?.messageId !== 'string') ctx.fail('invalid_output', 'Orca did not confirm the question.')
  const taskId = queued ? null : out.taskId
  state.log.push({ k: 'q', id: taskId ?? out.messageId, to: m.id, from, text: logText, at: ctx.now(), ...(queued ? { queued: true } : {}) })
  if (before) state.snaps[taskId ?? out.messageId] = before
  return { id: m.id, taskId, messageId: queued ? out.messageId : null, queued, delivery: queued ? 'queued' : String(out.delivery ?? '') }
}

const frame = (from, body) => `From ${String(from).replace(/^human:/u, '')}: ${body}\n\n(Council question. Answer concisely and honestly. Read-only: do not edit anything. Report your answer with worker_done; the result text is your answer.)`

/** One send per member. */
export const askMembers = (ctx, input) => locked(() => askMembersLocked(ctx, input))
async function askMembersLocked(ctx, { ids, text, from }) {
  const body = String(text ?? '').trim()
  if (!body) ctx.fail('invalid_params', 'Type a message first.')
  const state = await loadState(ctx.stateFile, ctx.repoRoot)
  if (!state.coordinator || !state.run) ctx.fail('conflict', 'There is no council yet. Start a member first.')
  const sent = []
  for (const id of ids) {
    const m = active(state).find((x) => x.id === id)
    if (!m) ctx.fail('invalid_params', `${id} is not a council member.`)
    sent.push(await sendOne(ctx, state, m, frame(from, body), body, from))
  }
  await saveState(ctx.stateFile, state)
  return sent
}

/**
 * Members are told to stay read-only but Orca has no permission flag for a worker, so the room watches instead: the repo's changed files
 * (path -> status:mtime:size) are noted when a question goes out and compared when its answer comes back. It cannot say WHO changed a file
 * (you may be editing the same worktree), so the warning says so.
 */
async function snapshotOf(ctx) {
  if (typeof ctx.snapshot !== 'function') return null
  try { return await ctx.snapshot() } catch { return null }
}
export function changedBetween(before, after) {
  if (!before || !after) return []
  const paths = new Set([...Object.keys(before), ...Object.keys(after)])
  return [...paths].filter((p) => before[p] !== after[p]).sort()
}

/** Answer a council member's own question (an Orca `ask`): the reply goes back through Orca, which wakes the member with it. */
export const answerMember = (ctx, input) => locked(() => answerMemberLocked(ctx, input))
async function answerMemberLocked(ctx, { messageId, text, from }) {
  const body = String(text ?? '').trim()
  if (!body) ctx.fail('invalid_params', 'Type your answer first.')
  const state = await loadState(ctx.stateFile, ctx.repoRoot)
  const ask = state.log.find((e) => e.k === 'a' && e.id === messageId && e.note === 'asks you')
  if (!ask) ctx.fail('invalid_params', 'That is not a question from a council member.')
  if (ask.answered) ctx.fail('conflict', 'You already answered that question.')
  const m = active(state).find((x) => x.id === ask.from)
  if (!m) ctx.fail('conflict', 'That council member has been released, so it can no longer be answered.')
  await ctx.orca(['orchestration', 'reply', '--id', messageId, '--body', body, '--from', state.coordinator, '--run', state.run, '--json'], 30000)
  ask.answered = true
  state.log.push({ k: 'q', id: `reply-${messageId}`, to: m.id, from, text: body, at: ctx.now() })
  await saveState(ctx.stateFile, state)
  return { id: m.id }
}

export const MAX_FOLLOWUPS = 3
const ROUND_GIVE_UP_MS = 10 * 60 * 1000
const clip = (t, n = 2500) => (t.length > n ? `${t.slice(0, n)}… (cut)` : t)
const labelOf = (state, m) => {
  const same = state.members.filter((x) => x.agent === m.agent)
  const base = m.agent === 'codex' ? 'Codex' : 'Claude'
  return same.length > 1 ? `${base} council member ${same.indexOf(m) + 1}` : `${base} council member`
}

/**
 * A Round: the question goes to every chosen member, then the room relays each member's answer to the others as a follow-up, `followups` times.
 * Members cannot message each other (Orca workers only report to their coordinator), so the room is the go-between and every relay shows in the room.
 */
export const startRound = (ctx, input) => locked(() => startRoundLocked(ctx, input))
async function startRoundLocked(ctx, { ids, text, from, followups }) {
  const body = String(text ?? '').trim()
  if (!body) ctx.fail('invalid_params', 'Type a message first.')
  const n = Math.floor(Number(followups))
  if (!Number.isFinite(n) || n < 1 || n > MAX_FOLLOWUPS) ctx.fail('invalid_params', `Choose 1 to ${MAX_FOLLOWUPS} follow-up rounds.`)
  const state = await loadState(ctx.stateFile, ctx.repoRoot)
  if (!state.coordinator || !state.run) ctx.fail('conflict', 'There is no council yet. Start a member first.')
  const members = [...new Set(ids)].map((id) => active(state).find((x) => x.id === id) ?? ctx.fail('invalid_params', `${id} is not a council member.`))
  if (members.length < 2) ctx.fail('invalid_params', 'A round needs at least two council members.')
  const sent = []
  for (const m of members) sent.push(await sendOne(ctx, state, m, frame(from, body), body, from))
  state.rounds.push({ id: `round_${sent[0].taskId ?? sent[0].messageId}`, from, question: body, ids: members.map((m) => m.id), done: 0, max: n, pending: Object.fromEntries(sent.map((x) => [x.taskId ?? `queued:${x.messageId}`, x.id])), answers: {}, sentAt: ctx.now(), finished: false })
  await saveState(ctx.stateFile, state)
  return sent
}

/** An answer arrived for `taskId`: if a round is waiting on it, keep the text for the relay. */
function noteRoundAnswer(state, taskId, memberId, text) {
  for (const r of state.rounds) {
    if (r.finished) continue
    const key = taskId && r.pending[taskId] === memberId ? taskId : Object.keys(r.pending).find((k) => k.startsWith('queued:') && r.pending[k] === memberId)   // a queued ask answers under the member's old task id
    if (key) { delete r.pending[key]; r.answers[memberId] = text }
  }
}

/** Rounds whose members have all answered (or timed out) get the next relay, or finish. Sends inside the caller's lock. */
async function advanceRounds(ctx, state) {
  let changed = false
  for (const r of state.rounds) {
    if (r.finished) continue
    try { if (await advanceRound(ctx, state, r)) changed = true } catch (e) {
      r.finished = true; changed = true
      state.log.push({ k: 'n', id: `${r.id}-stop`, text: `The round stopped: ${(e && e.message) || e}`, at: ctx.now() })
    }
  }
  return changed
}

async function advanceRound(ctx, state, r) {
  const live = r.ids.filter((id) => active(state).some((m) => m.id === id))
  for (const [taskId, id] of Object.entries(r.pending)) if (!live.includes(id)) delete r.pending[taskId]      // released members no longer count
  const timedOut = Object.keys(r.pending).length > 0 && Date.parse(ctx.now()) - Date.parse(r.sentAt) > ROUND_GIVE_UP_MS
  if (Object.keys(r.pending).length > 0 && !timedOut) return false
  if (timedOut) r.pending = {}
  const who = live.filter((id) => typeof r.answers[id] === 'string')
  if (r.done >= r.max || who.length < 2) {
    r.finished = true
    if (r.done < r.max) state.log.push({ k: 'n', id: `${r.id}-end`, text: `The round ended early: fewer than two members answered${timedOut ? ' in time' : ''}.`, at: ctx.now() })
    return true
  }
  const sent = {}
  for (const id of who) {
    const m = active(state).find((x) => x.id === id)
    const others = who.filter((o) => o !== id).map((o) => `- ${labelOf(state, active(state).find((x) => x.id === o))}: ${clip(r.answers[o])}`).join('\n')
    const framed = `From the room (round ${r.done + 2} of ${r.max + 1}): ${String(r.from).replace(/^human:/u, '')} asked: ${r.question}\n\nThe other council members answered:\n${others}\n\nRespond to them: say where you agree or disagree, and change your answer if they changed your mind. Concise and honest. Read-only: do not edit anything. Report with worker_done; the result text is your answer.`
    sent[id] = await sendOne(ctx, state, m, framed, `Round ${r.done + 2}: relayed ${who.length - 1} other answer${who.length === 2 ? '' : 's'} and asked for a response.`, r.from)
  }
  r.done += 1; r.answers = {}; r.pending = Object.fromEntries(Object.values(sent).map((x) => [x.taskId ?? `queued:${x.messageId}`, x.id])); r.sentAt = ctx.now()
  return true
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
export const syncCouncil = (ctx) => locked(() => syncCouncilLocked(ctx))
async function syncCouncilLocked(ctx) {
  const state = await loadState(ctx.stateFile, ctx.repoRoot)
  if (!state.run || active(state).length === 0) return false
  const inbox = result(await ctx.orca(['orchestration', 'inbox', '--terminal', `run:${state.run}`, '--limit', '200', '--full', '--json'], 30000))
  const messages = Array.isArray(inbox?.messages) ? [...inbox.messages].reverse() : []        // Orca lists newest first
  let changed = false
  let nowSnap
  const watch = async (taskId, member, msgId) => {                 // an answer came back: did the repo change since its question went out?
    const before = taskId ? state.snaps[taskId] : null
    if (!before) return
    delete state.snaps[taskId]
    nowSnap ??= (await snapshotOf(ctx)) ?? null
    const files = changedBetween(before, nowSnap)
    if (files.length) state.log.push({ k: 'n', id: `edit-${msgId}`, text: `Heads up: ${files.length} file${files.length === 1 ? '' : 's'} in this repository changed while ${labelOf(state, member)} was answering (${files.slice(0, 5).join(', ')}${files.length > 5 ? ', …' : ''}). Members are told to stay read-only, but nothing enforces it. This could also be you or another agent editing the same worktree.`, at: ctx.now() })
  }
  for (const msg of messages) {
    if (!msg || typeof msg.id !== 'string' || state.seen.includes(msg.id)) continue
    state.seen.push(msg.id); changed = true
    const sender = msg.from_handle ?? msg.from                  // Orca's inbox rows carry from_handle; payload is a JSON string
    const taskId = payloadOf(msg).taskId ?? null
    // A done report comes from the member's worker handle, but a question (`ask`) comes from the dispatch Orca made for the task it is on, which is
    // not the member's first dispatch. The task id is what joins it: it is the id our own send returned.
    const askedOf = taskId ? state.log.find((e) => e.k === 'q' && e.id === taskId) : null
    const member = state.members.find((m) => m.worker === sender) ?? (askedOf ? state.members.find((m) => m.id === askedOf.to) : null)
    if (!member) continue
    const body = String(msg.body ?? '').trim()
    if (msg.type === 'worker_done') {
      if (!member.joined && /^READY\b/u.test(body)) {
        member.joined = true; member.concordId = (CONCORD_ID.exec(body) || [null])[0]
        state.log.push({ k: 'join', id: msg.id, from: member.id, at: ctx.now() })
      } else { state.log.push({ k: 'a', id: msg.id, taskId, from: member.id, text: body, at: ctx.now() }); noteRoundAnswer(state, taskId, member.id, body); await watch(taskId, member, msg.id) }
    } else if (msg.type === 'escalation') {
      const got = await recoverAnswer(ctx, member)
      state.log.push({ k: 'a', id: msg.id, taskId, from: member.id, text: got ? got.text : '(It ended its turn without an answer. Ask again.)', at: got ? got.at : ctx.now(), note: got ? 'answered in its chat without reporting' : 'no answer' })
      if (got) noteRoundAnswer(state, taskId, member.id, got.text)
      await watch(taskId, member, msg.id)
    } else if (msg.type === 'question') {
      state.log.push({ k: 'a', id: msg.id, taskId: null, from: member.id, text: body, at: ctx.now(), note: 'asks you' })
    }
  }
  if (await advanceRounds(ctx, state)) changed = true
  if (changed) await saveState(ctx.stateFile, state)
  return changed
}

/** Hand a member back to Jim (Orca keeps its chat open as an ordinary chat) and drop it from the council. */
export const releaseMember = (ctx, id) => locked(() => releaseMemberLocked(ctx, id))
async function releaseMemberLocked(ctx, id) {
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
    if (e.k === 'q') return [{ id: `council-q-${e.id}`, from: e.from, to: e.to, content: e.text, at: e.at, status: 'sent', replyTo: null, receipt: e.queued ? 'queued: it was still working' : null }]
    if (e.k === 'a') return [{ id: `council-a-${e.id}`, from: e.from, to: askers.get(e.taskId) ?? 'human:me', content: e.note ? `${e.text}\n(${e.note})` : e.text, at: e.at, status: 'replied', replyTo: e.taskId ? `council-q-${e.taskId}` : null, receipt: null, ...(e.note === 'asks you' ? { ask: true, answered: e.answered === true } : {}) }]
    if (e.k === 'n') return [{ id: `council-n-${e.id}`, from: 'relay:council', to: 'human:me', content: e.text, at: e.at, status: 'replied', replyTo: null, receipt: null }]
    return []
  })
}
