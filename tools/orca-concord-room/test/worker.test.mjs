import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, realpath, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { EventEmitter } from 'node:events'
import { getMessages, validateRecords, validateExecutable, parseWho, sendMessage, callConcordTool, defaultSenderId, parseNicknames, withNickname } from '../worker/main.mjs'

test('reads genuine CLI JSON using fixed argv, without a shell, and sorts by time', async t => {
  const root = await realpath(await mkdtemp(path.join(tmpdir(), 'concord-orca-')))
  await mkdir(path.join(root, '.concord'))
  t.after(() => rm(root, { recursive: true, force: true }))
  let argv, options
  const runner = async (command, args, opts) => {
    assert.equal(command, 'concord')
    argv = args; options = opts
    return { stdout: JSON.stringify([
      { messageId:'m2', senderAgentId:'codex', recipientAgentId:'claude', content:'reply', createdAt:'2026-11-08T20:05:00Z', status:'replied' },
      { messageId:'m1', senderAgentId:'claude', recipientAgentId:'codex', content:'prompt', createdAt:'2026-11-08T20:04:00Z', status:'read', receipt: { ticks:'✓✓' } }
    ]) }
  }
  const result = await getMessages(root, 'concord', runner)
  assert.deepEqual(result.messages.map(x=>x.id), ['m1','m2'])
  assert.equal(result.messages[0].receipt, '✓✓')
  assert.deepEqual(argv, ['-C',root,'messages','--json','--limit','80'])
  assert.equal(options.shell, false)
  assert.equal(options.env.CONCORD_TELEMETRY_DISABLED,'1')
})

test('missing root refuses before executing CLI', async () => {
  await assert.rejects(getMessages('', 'concord', async()=>{throw Error('should not run')}), /Set Concord repository root/)
})
test('relative or shell-like executable rejected', () => {
  for (const binary of ['./concord','concord;rm','']) assert.throws(()=>validateExecutable(binary))
})
test('malformed message fails closed rather than being displayed', () => {
  assert.throws(()=>validateRecords([{content:'unattributed'}]), /malformed message/)
})
test('cloud-linked informational output fails closed', async t => {
  const root = await realpath(await mkdtemp(path.join(tmpdir(), 'concord-orca-')))
  await mkdir(path.join(root, '.concord'))
  t.after(() => rm(root,{recursive:true,force:true}))
  await assert.rejects(getMessages(root, 'concord', async()=>({stdout:'This repository is linked to Concord Cloud'})), /valid local JSON/)
})

test('folder without a Concord workspace fails closed and never runs the CLI', async t => {
  const root = await realpath(await mkdtemp(path.join(tmpdir(), 'concord-orca-')))
  t.after(() => rm(root, { recursive: true, force: true }))
  await assert.rejects(getMessages(root, 'concord', async () => { throw Error('should not run') }), /No Concord workspace/)
})

const roster = async () => ({ repoRoot: '/r', agents: [{ id: 'codex:bf5867fb', state: 'live/active' }] })
const never = async () => { throw Error('must not call Concord') }

test('parseWho reads live agent ids and states', () => {
  const agents = parseWho("Workspace: x\nWho's here\n  claude-code:2d46a09c live/active  -  (0s ago)\n  codex:bf5867fb     idle/active   -  (714s ago)\n")
  assert.deepEqual(agents, [{ id: 'claude-code:2d46a09c', state: 'live/active' }, { id: 'codex:bf5867fb', state: 'idle/active' }])
  assert.deepEqual(parseWho('Who\'s here\n  none\n'), [])
})

test('composer refuses bad input without calling Concord', async () => {
  const base = { repoRoot: '/r', executable: 'concord', senderId: 'human:jim' }
  const cases = [
    [{ to: 'codex:bf5867fb', content: '   ' }, /Type a message/],
    [{ to: '', content: 'hi' }, /Choose a recipient/],
    [{ to: 'codex:zzzz', content: 'hi' }, /not present/],
    [{ to: 'human:jim', content: 'hi' }, /yourself/],
    [{ to: 'codex:bf5867fb', content: 'x'.repeat(4001) }, /longer than/],
    [{ to: 'codex:bf5867fb', content: 'hi', senderId: 'rm -rf' }, /Sender id/]
  ]
  for (const [extra, pattern] of cases) {
    await assert.rejects(sendMessage({ ...base, ...extra }, { getAgents: roster, callTool: never }), pattern)
  }
})

test('composer sends a trimmed prompt as the human sender and returns Concord receipt data', async () => {
  let seen
  const callTool = async (command, env, name, args) => { seen = { command, env, name, args }; return { message_id: 'm1', status: 'sent', delivery: 'queued_pull', outlook: 'queued' } }
  const out = await sendMessage({ repoRoot: '/r', executable: '/x/bin/concord', senderId: 'human:jim', to: 'codex:bf5867fb', content: ' hello ' }, { getAgents: roster, callTool })
  assert.equal(seen.command, '/x/bin/concord-mcp')
  assert.equal(seen.name, 'update_work')
  assert.equal(seen.args.operation, 'prompt')
  assert.equal(seen.args.agent_id, 'human:jim')
  assert.equal(seen.args.to_agent_id, 'codex:bf5867fb')
  assert.equal(seen.args.content, 'hello')
  assert.match(seen.args.idempotency_key, /^room-/)
  assert.equal(seen.env.CONCORD_AGENT_ID, 'human:jim')
  assert.equal(seen.env.CONCORD_TELEMETRY_DISABLED, '1')
  assert.deepEqual(out, { messageId: 'm1', status: 'sent', delivery: 'queued_pull', outlook: 'queued', from: 'human:jim', to: 'codex:bf5867fb' })
})

test('composer fails closed when Concord does not confirm a message id', async () => {
  await assert.rejects(sendMessage({ repoRoot: '/r', executable: 'concord', senderId: 'human:jim', to: 'codex:bf5867fb', content: 'hi' }, { getAgents: roster, callTool: async () => ({}) }), /did not confirm/)
})

test('default sender id is human:<username> or a validated override', () => {
  assert.match(defaultSenderId(''), /^human:[a-z0-9_-]+$/)
  assert.equal(defaultSenderId('human:jim'), 'human:jim')
  assert.throws(() => defaultSenderId('jim'), /Sender id/)
})

function fakeMcp(reply) {
  return () => {
    const child = new EventEmitter()
    child.stdout = new EventEmitter(); child.stderr = new EventEmitter()
    child.killed = false; child.kill = () => { child.killed = true }
    const seen = []
    child.stdin = { write: (line) => {
      const msg = JSON.parse(line); seen.push(msg)
      if (msg.method === 'initialize') setImmediate(() => child.stdout.emit('data', Buffer.from(`${JSON.stringify({ jsonrpc: '2.0', id: 1, result: {} })}\n`)))
      if (msg.method === 'tools/call') setImmediate(() => child.stdout.emit('data', Buffer.from(`${JSON.stringify({ jsonrpc: '2.0', id: 2, ...reply })}\n`)))
    } }
    child.seen = seen
    fakeMcp.last = child
    return child
  }
}

test('MCP client initializes, calls the tool once, and returns structured content', async () => {
  const out = await callConcordTool('concord-mcp', {}, 'update_work', { a: 1 }, 2000, fakeMcp({ result: { structuredContent: { message_id: 'm9' } } }))
  assert.deepEqual(out, { message_id: 'm9' })
  assert.deepEqual(fakeMcp.last.seen.map(m => m.method), ['initialize', 'notifications/initialized', 'tools/call'])
  assert.equal(fakeMcp.last.killed, true)
})

test('MCP client surfaces a tool error and a missing executable as failures', async () => {
  await assert.rejects(callConcordTool('concord-mcp', {}, 'update_work', {}, 2000, fakeMcp({ result: { isError: true, content: [{ text: 'agent not found' }] } })), /agent not found/)
  const missing = () => { const c = new EventEmitter(); c.stdout = new EventEmitter(); c.stderr = new EventEmitter(); c.stdin = { write() {} }; c.kill = () => {}; setImmediate(() => c.emit('error', Object.assign(Error('x'), { code: 'ENOENT' }))); return c }
  await assert.rejects(callConcordTool('/nope/concord-mcp', {}, 'update_work', {}, 2000, missing), /not found/)
})

test('nicknames: parse is defensive, rename validates the id, clearing removes the name', () => {
  assert.deepEqual(parseNicknames('{"claude-code:2d46a09c":"Claude (terminal)","bad id":"x","codex:bf5867fb":"  "}'), { 'claude-code:2d46a09c': 'Claude (terminal)' })
  assert.deepEqual(parseNicknames('not json'), {})
  assert.deepEqual(parseNicknames('[1,2]'), {})
  const one = withNickname({}, 'codex:bf5867fb', ' Codex\u0007 (terminal) ')
  assert.deepEqual(one, { 'codex:bf5867fb': 'Codex (terminal)' })
  assert.equal(withNickname({ 'codex:bf5867fb': 'x' }, 'codex:bf5867fb', '')['codex:bf5867fb'], undefined)
  assert.equal(Object.values(withNickname({}, 'codex:bf5867fb', 'y'.repeat(100)))[0].length, 32)
  assert.throws(() => withNickname({}, 'rm -rf', 'x'), /Unknown agent id/)
})
