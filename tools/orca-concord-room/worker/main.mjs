// Concord Room Orca worker: read-only real Concord CLI, no shell or backend duplication.
import { execFile } from 'node:child_process'
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

export default function activate(orca) {
  orca.panels.handle('concord.messages', async () => getMessages(
    await orca.settings.get('repoRoot'),
    await orca.settings.get('concordExecutable')
  ))
}
