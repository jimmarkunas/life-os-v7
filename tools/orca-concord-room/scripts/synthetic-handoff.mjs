#!/usr/bin/env node
// Local synthetic stand-in for a handoff arriving from Claude Chat or ChatGPT (LIFE-375).
// Writes one UNSIGNED handoff file into the handoff folder. No network, no Oracle, no Buzz.
//
//   node scripts/synthetic-handoff.mjs --init-policy
//   node scripts/synthetic-handoff.mjs --sender chatgpt-chat --jira LIFE-411 \
//        --scope "Check the pilot status" --brief "Read the Jira issue and report its release state." \
//        --target any            (or: agent:claude-code:2d46a09c | new-chat:codex | new-chat:claude)
//
// Options: --dir <folder> (default ~/.concord-pilot/handoffs)
import { mkdir, writeFile } from 'node:fs/promises'
import { randomBytes } from 'node:crypto'
import os from 'node:os'
import path from 'node:path'

const args = process.argv.slice(2)
const flag = (name) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : undefined }
const dir = flag('dir') ?? path.join(os.homedir(), '.concord-pilot', 'handoffs')
await mkdir(dir, { recursive: true })

if (args.includes('--init-policy')) {
  const policy = { senders: ['chatgpt-chat', 'claude-chat'], targets: { agents: true, terminals: true, newChat: ['claude', 'codex'] }, requireAccept: true }
  await writeFile(path.join(dir, 'policy.json'), `${JSON.stringify(policy, null, 2)}\n`, { mode: 0o600 })
  console.log(`wrote ${path.join(dir, 'policy.json')}`)
  if (!flag('jira')) process.exit(0)
}

const sender = flag('sender') ?? 'chatgpt-chat'
const jiraKey = flag('jira')
const scope = flag('scope')
const brief = flag('brief')
const targetArg = flag('target') ?? 'any'
if (!jiraKey || !scope || !brief) { console.error('Need --jira, --scope and --brief (or only --init-policy).'); process.exit(2) }
let target
if (targetArg === 'any') target = { kind: 'any' }
else if (targetArg.startsWith('agent:')) target = { kind: 'agent', id: targetArg.slice(6) }
else if (targetArg.startsWith('new-chat:')) target = { kind: 'new-chat', agent: targetArg.slice(9) }
else { console.error('Bad --target'); process.exit(2) }

const id = `hf-${randomBytes(6).toString('hex')}`
const handoff = { id, sender, jiraKey, scope, brief, nonce: randomBytes(9).toString('hex'), createdAt: new Date().toISOString(), target, synthetic: true, signed: false }
await writeFile(path.join(dir, `${id}.json`), `${JSON.stringify(handoff, null, 2)}\n`, { mode: 0o600 })
console.log(`wrote handoff ${id} (${sender} -> ${targetArg}, ${jiraKey})`)
