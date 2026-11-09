// Concord Room Orca worker: read-only real Concord CLI, no shell or backend duplication.
import { execFile, spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import os from 'node:os'
import { promisify } from 'node:util'
import { stat, realpath } from 'node:fs/promises'
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
    return { agents, me: defaultSenderId(s.senderId) }
  })
  orca.panels.handle('concord.send', async (input) => {
    const s = await settings()
    return sendMessage({ ...s, to: input?.to, content: input?.content })
  })
}
