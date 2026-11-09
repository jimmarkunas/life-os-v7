// Concord Room Orca worker: read-only real Concord CLI, no shell or backend duplication.
import { execFile, spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import os from 'node:os'
import { existsSync } from 'node:fs'
import { promisify } from 'node:util'
import { stat, realpath, readFile, writeFile, mkdir, lstat, readdir, appendFile } from 'node:fs/promises'
import path from 'node:path'

const execFileAsync = promisify(execFile)
const MAX_MESSAGES = 80
function fail(code, detail) {
  const error = new Error(detail)
  error.code = code
  throw error
}

export function validateExecutable(value) {
  if (typeof value !== 'string' || !value.trim() || /[\0\n\r]/u.test(value)) {
    fail('invalid_params', 'Configure one Concord executable (no command arguments).')
  }
  const binary = value.trim()
  if (binary.includes('/') && !path.isAbsolute(binary)) fail('invalid_params', 'Concord executable must be a command name or absolute path.')
  if (!binary.includes('/') && !/^[\w.-]+$/u.test(binary)) fail('invalid_params', 'Concord executable must be a simple command name.')
  return binary
}

export async function validateRepo(value) {
  if (typeof value !== 'string' || !value.trim()) fail('invalid_params', 'Set Concord repository root in Orca plugin settings.')
  if (!path.isAbsolute(value)) fail('invalid_params', 'Concord repo root must be an absolute path.')
  const root = await realpath(value).catch(() => fail('invalid_params', 'Configured repository path does not exist.'))
  if (!(await stat(root)).isDirectory()) fail('invalid_params', 'Configured repository root is not a folder.')
  // The Concord CLI creates a new empty store in any folder it is pointed at, even for
  // read-only commands. Require an existing workspace so a mistyped path fails closed
  // instead of showing a healthy "0 messages" feed and leaving a stray .concord behind.
  const store = await stat(path.join(root, '.concord')).catch(() => null)
  if (!store?.isDirectory()) fail('invalid_params', 'No Concord workspace (.concord) in the configured repository. Run concord setup there first.')
  return root
}

export function validateRecords(value) {
  if (!Array.isArray(value)) fail('invalid_output', 'Concord did not return an array of messages.')
  if (value.length > MAX_MESSAGES) fail('invalid_output', 'Concord exceeded the allowed message count.')
  return value.map((msg) => {
    if (!msg || typeof msg !== 'object' || typeof msg.messageId !== 'string' ||
        typeof msg.senderAgentId !== 'string' || typeof msg.recipientAgentId !== 'string' ||
        typeof msg.content !== 'string' || typeof msg.createdAt !== 'string' || typeof msg.status !== 'string') {
      fail('invalid_output', 'Concord returned a malformed message record.')
    }
    return {
      id: msg.messageId,
      from: msg.senderAgentId,
      to: msg.recipientAgentId,
      content: msg.content,
      at: msg.createdAt,
      status: msg.status,
      replyTo: typeof msg.replyToMessageId === 'string' ? msg.replyToMessageId : null,
      receipt: typeof msg.receipt?.ticks === 'string' ? msg.receipt.ticks : null
    }
  }).sort((a,b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id))
}

export async function getMessages(repoRoot, executable, runner = execFileAsync) {
  const root = await validateRepo(repoRoot)
  const binary = validateExecutable(executable)
  let output
  try {
    output = await runner(binary, ['-C', root, 'messages', '--json', '--limit', String(MAX_MESSAGES)], {
      cwd: root, env: { ...process.env, CONCORD_TELEMETRY_DISABLED: '1' },
      timeout: 8000, maxBuffer: 1024 * 1024, windowsHide: true, shell: false
    })
  } catch (err) {
    const detail = err?.code === 'ENOENT'
      ? `Concord executable not found: ${binary}. Install Concord or set its full path.`
      : `Concord read failed (${err?.code ?? 'runtime_error'}). No data was changed.`
    fail('unavailable', detail)
  }
  try {
    return { repoRoot: root, messages: validateRecords(JSON.parse(output.stdout)) }
  } catch (err) {
    if (err.code === 'invalid_output') throw err
    fail('invalid_output', 'Concord did not return valid local JSON. Concord Cloud-linked mode is unsupported.')
  }
}

const AGENT_ID = /^[a-z0-9-]+:[A-Za-z0-9_-]+$/u
const MAX_CONTENT = 4000

/** Parse `concord who` text ("  claude-code:2d46a09c live/active ...") into live agent ids. */
export function parseWho(text) {
  const agents = []
  for (const line of String(text).split('\n')) {
    const match = /^\s+([a-z0-9-]+:[A-Za-z0-9_-]+)\s+(\S+)/u.exec(line)
    if (match) agents.push({ id: match[1], state: match[2] })
  }
  return agents
}

export function defaultSenderId(configured) {
  const value = typeof configured === 'string' ? configured.trim() : ''
  if (value) {
    if (!AGENT_ID.test(value)) fail('invalid_params', 'Sender id must look like human:name.')
    return value
  }
  const user = os.userInfo().username.toLowerCase().replace(/[^a-z0-9_-]/gu, '') || 'me'
  return `human:${user}`
}

export async function getAgents(repoRoot, executable, runner = execFileAsync) {
  const root = await validateRepo(repoRoot)
  const binary = validateExecutable(executable)
  let output
  try {
    output = await runner(binary, ['-C', root, 'who'], {
      cwd: root, env: { ...process.env, CONCORD_TELEMETRY_DISABLED: '1' },
      timeout: 8000, maxBuffer: 256 * 1024, windowsHide: true, shell: false
    })
  } catch (err) {
    fail('unavailable', `Concord who failed (${err?.code ?? 'runtime_error'}).`)
  }
  return { repoRoot: root, agents: parseWho(output.stdout) }
}

function mcpExecutable(concordExecutable) {
  const binary = validateExecutable(concordExecutable)
  return path.isAbsolute(binary) ? path.join(path.dirname(binary), 'concord-mcp') : 'concord-mcp'
}

/** Minimal newline-delimited JSON-RPC client for one tool call to the Concord MCP server (stdio, no shell). */
export function callConcordTool(command, env, name, args, timeoutMs = 15000, spawnFn = spawn) {
  return new Promise((resolve, reject) => {
    const child = spawnFn(command, [], { env, stdio: ['pipe', 'pipe', 'pipe'], shell: false, windowsHide: true })
    let buffer = ''
    let settled = false
    const done = (fn, value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      try { child.kill() } catch { /* already gone */ }
      fn(value)
    }
    const timer = setTimeout(() => done(reject, Object.assign(new Error('Concord did not answer in time. Nothing was confirmed sent.'), { code: 'unavailable' })), timeoutMs)
    const send = (message) => child.stdin.write(`${JSON.stringify(message)}\n`)
    child.on('error', (err) => done(reject, Object.assign(new Error(err?.code === 'ENOENT' ? `Concord MCP executable not found: ${command}` : 'Could not start Concord MCP.'), { code: 'unavailable' })))
    child.on('exit', () => done(reject, Object.assign(new Error('Concord MCP exited before answering. Nothing was confirmed sent.'), { code: 'unavailable' })))
    child.stdout.on('data', (chunk) => {
      buffer += chunk.toString('utf8')
      let index
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index).trim()
        buffer = buffer.slice(index + 1)
        if (!line) continue
        let message
        try { message = JSON.parse(line) } catch { continue }
        if (message.id === 1) {
          send({ jsonrpc: '2.0', method: 'notifications/initialized' })
          send({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name, arguments: args } })
        } else if (message.id === 2) {
          if (message.error || message.result?.isError) {
            const text = message.error?.message ?? message.result?.content?.map?.((c) => c.text).join(' ') ?? 'Concord rejected the call.'
            done(reject, Object.assign(new Error(String(text).slice(0, 300)), { code: 'action_failed' }))
          } else {
            done(resolve, message.result?.structuredContent ?? null)
          }
        }
      }
    })
    send({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'orca-concord-room', version: '0.2.0' } } })
  })
}

let sending = false

/** Send one prompt from the human to a currently-present agent. Fails closed; never claims delivery it cannot show. */
export async function sendMessage({ repoRoot, executable, senderId, to, content }, deps = {}) {
  const who = deps.getAgents ?? getAgents
  const call = deps.callTool ?? callConcordTool
  const text = typeof content === 'string' ? content.trim() : ''
  if (!text) fail('invalid_params', 'Type a message first.')
  if (text.length > MAX_CONTENT) fail('invalid_params', `Message is longer than ${MAX_CONTENT} characters.`)
  if (typeof to !== 'string' || !AGENT_ID.test(to)) fail('invalid_params', 'Choose a recipient agent.')
  const sender = defaultSenderId(senderId)
  if (to === sender) fail('invalid_params', 'Cannot send a message to yourself.')
  const roster = await who(repoRoot, executable)
  if (!roster.agents.some((agent) => agent.id === to)) fail('invalid_params', `${to} is not present in this Concord workspace right now.`)
  if (sending) fail('conflict', 'Another message is still being sent.')
  sending = true
  try {
    const result = await call(mcpExecutable(executable), {
      ...process.env, CONCORD_REPO_ROOT: roster.repoRoot, CONCORD_AGENT_ID: sender, CONCORD_TELEMETRY_DISABLED: '1'
    }, 'update_work', { operation: 'prompt', agent_id: sender, to_agent_id: to, content: text, idempotency_key: `room-${randomUUID()}` })
    if (!result || typeof result.message_id !== 'string') fail('invalid_output', 'Concord did not confirm the message.')
    return { messageId: result.message_id, status: String(result.status ?? 'sent'), delivery: String(result.delivery ?? ''), outlook: String(result.outlook ?? ''), from: sender, to }
  } finally {
    sending = false
  }
}

const PROJECT_KEY = /^[A-Z][A-Z0-9]{1,9}$/u
const ISSUE_KEY = /^[A-Z][A-Z0-9]{1,9}-\d+$/u
const LABEL = /^[\w.-]{1,40}$/u

function orcaBin() {
  const fromEnv = process.env.ORCA_CLI_COMMAND?.trim()
  if (fromEnv) return fromEnv
  return existsSync('/usr/local/bin/orca') ? '/usr/local/bin/orca' : 'orca'
}

async function orcaJson(args, runner = execFileAsync) {
  let out
  try {
    out = await runner(orcaBin(), args, { timeout: 20000, maxBuffer: 1024 * 1024, shell: false, windowsHide: true, env: process.env })
  } catch (err) {
    fail('unavailable', err?.code === 'ENOENT' ? 'Orca CLI not found.' : `Orca Jira call failed (${err?.code ?? 'runtime_error'}). Nothing was confirmed created.`)
  }
  try { return JSON.parse(out.stdout) } catch { fail('invalid_output', 'Orca did not return JSON.') }
}

export async function jiraProjects(runner) {
  const parsed = await orcaJson(['jira', 'project', 'list', '--json'], runner)
  const list = Array.isArray(parsed?.result) ? parsed.result : []
  return list.filter((p) => typeof p?.key === 'string' && PROJECT_KEY.test(p.key)).map((p) => ({ key: p.key, name: String(p.name ?? p.key) }))
}

export async function jiraTypes(project, runner) {
  if (!PROJECT_KEY.test(String(project))) fail('invalid_params', 'Choose a Jira project.')
  const parsed = await orcaJson(['jira', 'project', 'types', '--project', project, '--json'], runner)
  const list = Array.isArray(parsed?.result) ? parsed.result : []
  return list.filter((t) => /^\d+$/u.test(String(t?.id)) && !t.subtask).map((t) => ({ id: String(t.id), name: String(t.name ?? t.id) }))
}

/** Create one Jira issue through Orca's own Jira connection. Only on an explicit confirm from the panel. */
export async function createJira(input, deps = {}) {
  if (input?.confirm !== true) fail('invalid_params', 'Creating a Jira issue needs an explicit confirm.')
  const project = String(input.project ?? '')
  if (!PROJECT_KEY.test(project)) fail('invalid_params', 'Choose a Jira project.')
  const typeId = String(input.typeId ?? '')
  if (!/^\d+$/u.test(typeId)) fail('invalid_params', 'Choose an issue type.')
  const title = String(input.title ?? '').replace(/[\u0000-\u001f]/gu, ' ').trim()
  if (!title) fail('invalid_params', 'Give the ticket a title.')
  if (title.length > 255) fail('invalid_params', 'Title is longer than 255 characters.')
  const body = String(input.body ?? '')
  if (body.length > 20000) fail('invalid_params', 'Description is longer than 20000 characters.')
  const labels = Array.isArray(input.labels) ? input.labels.map(String).filter(Boolean) : []
  if (labels.length > 10 || labels.some((l) => !LABEL.test(l))) fail('invalid_params', 'Labels must be up to 10 short words (letters, numbers, - _ .).')
  const parent = input.parent ? String(input.parent).trim() : ''
  if (parent && !ISSUE_KEY.test(parent)) fail('invalid_params', 'Parent must look like LIFE-125.')
  const fields = {}
  if (labels.length) fields.labels = labels
  if (parent) fields.parent = { key: parent }
  const args = ['jira', 'create', '--project', project, '--type', typeId, '--title', title, '--body-file', '-', '--json']
  if (Object.keys(fields).length) args.push('--fields-json', JSON.stringify(fields))
  const run = deps.runWithInput ?? runWithInput
  let out
  try { out = await run(orcaBin(), args, body) } catch (err) {
    fail('unavailable', err?.code === 'ENOENT' ? 'Orca CLI not found.' : 'Orca could not run the Jira create. Check Jira before trying again, in case it went through.')
  }
  const text = String(out ?? '')
  let key
  try { const j = JSON.parse(text); key = j?.result?.key ?? j?.result?.issue?.key ?? j?.result?.issueKey ?? j?.key } catch { /* fall through to a plain match */ }
  if (typeof key !== 'string' || !ISSUE_KEY.test(key)) key = text.match(/\b[A-Z][A-Z0-9]{1,9}-\d+\b/u)?.[0]
  if (!key) fail('invalid_output', `Orca did not return an issue key; check Jira before retrying. Output: ${text.slice(0, 200)}`)
  const sites = await (deps.sites ?? jiraSiteUrl)()
  return { key, url: sites ? `${sites}/browse/${key}` : '' }
}

async function jiraSiteUrl() {
  try {
    const parsed = await orcaJson(['jira', 'connection', '--json'])
    const r = parsed?.result
    const site = r?.sites?.find((x) => x.id === (r.selectedSiteId ?? r.activeSiteId)) ?? r?.sites?.[0]
    return typeof site?.siteUrl === 'string' && /^https:\/\//u.test(site.siteUrl) ? site.siteUrl.replace(/\/$/u, '') : ''
  } catch { return '' }
}

function runWithInput(command, args, input) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['pipe', 'pipe', 'pipe'], shell: false, windowsHide: true, env: process.env })
    let out = ''
    const timer = setTimeout(() => { child.kill(); reject(Object.assign(new Error('timeout'), { code: 'ETIMEDOUT' })) }, 30000)
    child.stdout.on('data', (d) => { out += d.toString('utf8') })
    child.on('error', (e) => { clearTimeout(timer); reject(e) })
    child.on('close', (code) => { clearTimeout(timer); code === 0 ? resolve(out) : reject(Object.assign(new Error('exit ' + code), { code: 'EXIT' + code })) })
    child.stdin.end(input)
  })
}


// ---------------------------------------------------------------------------------------------
// Handoffs (LIFE-375 / LIFE-403). A handoff is a POINTER to a Jira issue, never an order: nothing
// is delivered until Jim accepts it in the room, and the receiving agent must check the Jira
// issue's release state and writer claim before doing anything. This prototype reads handoffs
// from a LOCAL folder (a stand-in for the Buzz channel): no network, no Oracle, no Buzz.
// ---------------------------------------------------------------------------------------------
const HANDOFF_ID = /^[A-Za-z0-9_-]{6,64}$/u
const HANDOFF_NONCE = /^[A-Za-z0-9_-]{8,64}$/u
const HANDOFF_SENDERS = ['chatgpt-chat', 'claude-chat']
const SENDER_LABEL = { 'chatgpt-chat': 'ChatGPT', 'claude-chat': 'Claude Chat' }
const RELAY_SENDER = 'relay:orca-room'
const MAX_HANDOFF_BYTES = 20000
const MAX_HANDOFFS = 50

export function defaultHandoffDir(setting) {
  const v = typeof setting === 'string' ? setting.trim() : ''
  if (v) {
    if (!path.isAbsolute(v)) fail('invalid_params', 'Handoff folder must be an absolute path.')
    return v
  }
  return path.join(os.homedir(), '.concord-pilot', 'handoffs')
}

/** Policy file, re-read on every call; missing or malformed means every handoff is refused. */
export async function readPolicy(dir) {
  let raw
  try { raw = JSON.parse(await readFile(path.join(dir, 'policy.json'), 'utf8')) } catch { fail('unavailable', 'Handoff policy is missing or unreadable, so every handoff is refused.') }
  const senders = raw?.senders
  const newChat = raw?.targets?.newChat
  const agents = raw?.targets?.agents
  const ok = Array.isArray(senders) && senders.length > 0 && senders.every((x) => HANDOFF_SENDERS.includes(x)) &&
    Array.isArray(newChat) && newChat.every((x) => ['claude', 'codex'].includes(x)) &&
    typeof agents === 'boolean' && raw?.requireAccept === true
  if (!ok) fail('unavailable', 'Handoff policy is malformed, so every handoff is refused.')
  return { senders: new Set(senders), newChat: new Set(newChat), agents, requireAccept: true }
}

const oneLine = (v, max) => String(v ?? '').replace(/[\u0000-\u001f\u007f]/gu, ' ').replace(/\s+/gu, ' ').trim().slice(0, max)
const multiLine = (v, max) => String(v ?? '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/gu, '').trim().slice(0, max)

export function validateHandoff(h, fileId, policy) {
  if (!h || typeof h !== 'object' || Array.isArray(h)) fail('invalid_output', 'Malformed handoff.')
  const id = String(h.id ?? '')
  if (!HANDOFF_ID.test(id) || id !== fileId) fail('invalid_output', 'Handoff id is missing or does not match its file name.')
  if (!policy.senders.has(h.sender)) fail('invalid_output', `Sender "${oneLine(h.sender, 30)}" is not allowed by the handoff policy.`)
  const jiraKey = String(h.jiraKey ?? '')
  if (!ISSUE_KEY.test(jiraKey)) fail('invalid_output', 'Handoff needs a Jira key like LIFE-411.')
  const scope = oneLine(h.scope, 300)
  const brief = multiLine(h.brief, 1500)
  if (!scope || !brief) fail('invalid_output', 'Handoff needs a scope and a brief.')
  const nonce = String(h.nonce ?? '')
  if (!HANDOFF_NONCE.test(nonce)) fail('invalid_output', 'Handoff needs a nonce.')
  const createdAt = String(h.createdAt ?? '')
  if (Number.isNaN(Date.parse(createdAt))) fail('invalid_output', 'Handoff needs a valid createdAt.')
  const t = h.target ?? {}
  let target
  if (t.kind === 'agent') {
    if (!policy.agents || !AGENT_ID.test(String(t.id ?? ''))) fail('invalid_output', 'Handoff names a target agent the policy does not allow.')
    target = { kind: 'agent', id: String(t.id) }
  } else if (t.kind === 'new-chat') {
    if (!policy.newChat.has(t.agent)) fail('invalid_output', 'Handoff asks for a new chat the policy does not allow.')
    target = { kind: 'new-chat', agent: t.agent }
  } else if (t.kind === 'any') {
    target = { kind: 'any' }
  } else {
    fail('invalid_output', 'Handoff has no valid target.')
  }
  return { id, sender: h.sender, jiraKey, scope, brief, nonce, createdAt, target }
}

async function readState(dir, id) {
  try { return JSON.parse(await readFile(path.join(dir, 'state', `${id}.json`), 'utf8')) } catch { return null }
}

async function writeState(dir, id, state) {
  await mkdir(path.join(dir, 'state'), { recursive: true })
  await writeFile(path.join(dir, 'state', `${id}.json`), `${JSON.stringify(state)}\n`, { mode: 0o600 })
}

/** One JSON audit line per operation. Never contains the scope, brief or any message text. */
export async function audit(dir, entry) {
  try {
    await mkdir(dir, { recursive: true })
    await appendFile(path.join(dir, 'audit.log'), `${JSON.stringify({ ts: new Date().toISOString(), ...entry })}\n`, { mode: 0o600 })
  } catch { /* an audit write failure must not hide the operation's outcome from the caller */ }
}

const jiraCache = new Map()
async function jiraStatusOf(key) {
  const hit = jiraCache.get(key)
  if (hit && Date.now() - hit.at < 60000) return hit.status
  let status = null
  try {
    const parsed = await orcaJson(['jira', 'issue', key, '--json'])
    status = typeof parsed?.result?.status?.name === 'string' ? parsed.result.status.name : null
  } catch { status = null }
  jiraCache.set(key, { at: Date.now(), status })
  return status
}

/** Every handoff in the folder with its state. Invalid, duplicate and over-limit files are reported, never trusted. */
export async function listHandoffs(dirSetting, deps = {}) {
  const dir = defaultHandoffDir(dirSetting)
  const policy = await readPolicy(dir)
  let names
  try { names = (await readdir(dir)).filter((n) => n.endsWith('.json') && n !== 'policy.json').sort().slice(0, MAX_HANDOFFS) } catch { return { dir, handoffs: [] } }
  const entries = []
  for (const name of names) {
    const fileId = name.slice(0, -5)
    const full = path.join(dir, name)
    try {
      const info = await lstat(full)
      if (!info.isFile() || info.size > MAX_HANDOFF_BYTES) fail('invalid_output', 'Handoff file is not a plain file or is too large.')
      const h = validateHandoff(JSON.parse(await readFile(full, 'utf8')), fileId, policy)
      entries.push(h)
    } catch (err) {
      entries.push({ id: fileId, invalid: true, detail: err?.code === 'invalid_output' ? err.message : 'Handoff file could not be read as JSON.' })
    }
  }
  const seen = new Map()
  const out = []
  for (const h of [...entries].sort((a, b) => String(a.createdAt ?? '').localeCompare(String(b.createdAt ?? '')) || a.id.localeCompare(b.id))) {
    if (h.invalid) { out.push({ id: h.id, status: 'invalid', detail: h.detail }); continue }
    if (seen.has(h.nonce)) { out.push({ ...h, status: 'duplicate', detail: `Same nonce as handoff ${seen.get(h.nonce)}; ignored.` }); continue }
    seen.set(h.nonce, h.id)
    const state = await readState(dir, h.id)
    out.push({ ...h, status: state?.status ?? 'pending', detail: state?.detail ?? '', messageId: state?.messageId ?? '', jiraStatus: await (deps.jiraStatus ?? jiraStatusOf)(h.jiraKey) })
  }
  return { dir, handoffs: out.reverse() }
}

export function handoffPrompt(h) {
  return [
    `HANDOFF from ${SENDER_LABEL[h.sender] ?? h.sender} (handoff ${h.id}). This is a pointer, not an order.`,
    `Jira issue: ${h.jiraKey}`,
    `Scope: ${h.scope}`,
    `Brief: ${h.brief}`,
    '',
    `Rules: this message does not authorize any work. Before doing anything, read Jira issue ${h.jiraKey}: it must be released (Agent Go) and you must hold its writer claim.`,
    'If it is not released or not claimed, reply "REFUSED: <reason>" and do nothing else.',
    'Do not edit files unless that Jira issue itself allows it. Reply to this message with your decision and a one-line status.'
  ].join('\n')
}

async function launchChat({ agent, worktreeRoot, prompt }, run = execFileAsync) {
  const out = await run(orcaBin(), ['agent', 'launch', '--agent', agent === 'codex' ? 'codex' : 'claude', '--worktree', `path:${worktreeRoot}`, '--surface', 'chat', '--prompt', prompt, '--json'], { timeout: 30000, maxBuffer: 1024 * 1024, shell: false, windowsHide: true, env: process.env })
  let parsed
  try { parsed = JSON.parse(out.stdout) } catch { fail('invalid_output', 'Orca did not return JSON for the new chat.') }
  if (parsed?.ok !== true) fail('action_failed', 'Orca did not start the chat.')
  return `new ${agent} chat ${parsed?.result?.outcome?.sessionId ?? ''} (prompt ${parsed?.result?.prompt?.outcome ?? 'sent'})`.trim()
}

/** Jim's Accept: deliver one pending handoff to one allowed target. Everything is checked again here. */
export async function acceptHandoff(input, deps = {}) {
  const dir = defaultHandoffDir(input.handoffDir)
  const { handoffs } = await listHandoffs(input.handoffDir, deps)
  const h = handoffs.find((x) => x.id === input.id)
  if (!h || h.status === 'invalid' || h.status === 'duplicate') fail('invalid_params', 'That handoff is not valid.')
  if (h.status !== 'pending') fail('conflict', `That handoff is already ${h.status}.`)
  const policy = await readPolicy(dir)
  const want = input.target ?? {}
  let target
  if (h.target.kind === 'any') target = want
  else target = h.target
  if (h.target.kind !== 'any' && want.kind && JSON.stringify(want) !== JSON.stringify(h.target)) fail('invalid_params', 'This handoff names its target; it cannot be redirected.')
  let detail
  let status
  let messageId = ''
  try {
    if (target.kind === 'agent') {
      if (!policy.agents) fail('invalid_params', 'The policy does not allow handoffs to open agents.')
      const result = await (deps.send ?? sendMessage)({ repoRoot: input.repoRoot, executable: input.executable, senderId: RELAY_SENDER, to: target.id, content: handoffPrompt(h) }, deps)
      status = result.delivery === 'delivered' ? 'delivered' : 'queued'
      messageId = typeof result.messageId === 'string' ? result.messageId : ''
      detail = status === 'delivered' ? `Delivered to ${target.id}.` : `Queued for ${target.id}, not delivered yet (${result.delivery || result.status}).`
    } else if (target.kind === 'new-chat') {
      if (!policy.newChat.has(target.agent)) fail('invalid_params', 'The policy does not allow that kind of new chat.')
      const root = await validateRepo(input.repoRoot)
      detail = await (deps.launch ?? launchChat)({ agent: target.agent, worktreeRoot: root, prompt: handoffPrompt(h) })
      status = 'started'
    } else {
      fail('invalid_params', 'Choose where this handoff goes.')
    }
  } catch (err) {
    await audit(dir, { op: 'accept', id: h.id, sender: h.sender, jira: h.jiraKey, target: target.kind === 'agent' ? { kind: 'agent', id: target.id } : target, outcome: 'failed', code: err?.code ?? 'error' })
    throw err
  }
  await writeState(dir, h.id, { status, detail, messageId, at: new Date().toISOString() })
  await audit(dir, { op: 'accept', id: h.id, sender: h.sender, jira: h.jiraKey, target: target.kind === 'agent' ? { kind: 'agent', id: target.id } : target, outcome: status })
  return { id: h.id, status, detail }
}

export async function rejectHandoff(input, deps = {}) {
  const dir = defaultHandoffDir(input.handoffDir)
  const { handoffs } = await listHandoffs(input.handoffDir, deps)
  const h = handoffs.find((x) => x.id === input.id)
  if (!h || h.status === 'invalid' || h.status === 'duplicate') fail('invalid_params', 'That handoff is not valid.')
  if (h.status !== 'pending') fail('conflict', `That handoff is already ${h.status}.`)
  await writeState(dir, h.id, { status: 'rejected', detail: 'Rejected by Jim.', at: new Date().toISOString() })
  await audit(dir, { op: 'reject', id: h.id, sender: h.sender, jira: h.jiraKey, outcome: 'rejected' })
  return { id: h.id, status: 'rejected' }
}

const MAX_NAME = 32

/** Nicknames are stored as one JSON string in the plugin's own settings: { "<agent id>": "<name>" }. */
export function parseNicknames(raw) {
  let parsed
  try { parsed = typeof raw === 'string' && raw.trim() ? JSON.parse(raw) : {} } catch { parsed = {} }
  const out = {}
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    for (const [id, name] of Object.entries(parsed)) {
      if (AGENT_ID.test(id) && typeof name === 'string' && name.trim()) out[id] = cleanName(name)
    }
  }
  return out
}

function cleanName(name) {
  return String(name).replace(/[\u0000-\u001f\u007f]/gu, '').trim().slice(0, MAX_NAME)
}

export function withNickname(current, id, name) {
  if (typeof id !== 'string' || !AGENT_ID.test(id)) fail('invalid_params', 'Unknown agent id.')
  const next = { ...current }
  const cleaned = typeof name === 'string' ? cleanName(name) : ''
  if (cleaned) next[id] = cleaned
  else delete next[id]
  return next
}

export default function activate(orca) {
  const settings = async () => ({
    repoRoot: await orca.settings.get('repoRoot'),
    executable: await orca.settings.get('concordExecutable'),
    senderId: await orca.settings.get('senderId')
  })
  orca.panels.handle('concord.messages', async () => {
    const s = await settings()
    return getMessages(s.repoRoot, s.executable)
  })
  orca.panels.handle('concord.agents', async () => {
    const s = await settings()
    const { agents } = await getAgents(s.repoRoot, s.executable)
    return { agents, me: defaultSenderId(s.senderId), nicknames: parseNicknames(await orca.settings.get('nicknames')) }
  })
  const handoffSettings = async () => ({
    handoffDir: await orca.settings.get('handoffDir'),
    repoRoot: await orca.settings.get('repoRoot'),
    executable: await orca.settings.get('concordExecutable')
  })
  orca.panels.handle('handoff.list', async () => listHandoffs((await handoffSettings()).handoffDir))
  orca.panels.handle('handoff.accept', async (input) => acceptHandoff({ ...(await handoffSettings()), id: input?.id, target: input?.target }))
  orca.panels.handle('handoff.reject', async (input) => rejectHandoff({ ...(await handoffSettings()), id: input?.id }))
  orca.panels.handle('jira.projects', async () => ({ projects: await jiraProjects() }))
  orca.panels.handle('jira.types', async (input) => ({ types: await jiraTypes(input?.project) }))
  orca.panels.handle('jira.create', async (input) => createJira(input))
  orca.panels.handle('concord.rename', async (input) => {
    const next = withNickname(parseNicknames(await orca.settings.get('nicknames')), input?.id, input?.name)
    await orca.settings.set('nicknames', JSON.stringify(next))
    return { nicknames: next }
  })
  orca.panels.handle('concord.send', async (input) => {
    const s = await settings()
    return sendMessage({ ...s, to: input?.to, content: input?.content })
  })
}
