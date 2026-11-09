import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, realpath, rm, writeFile, readFile as readF, symlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { EventEmitter } from 'node:events'
import { readFileSync } from 'node:fs'
import { getMessages, validateRecords, validateExecutable, parseWho, sendMessage, callConcordTool, defaultSenderId, parseNicknames, withNickname, createJira, jiraProjects, jiraTypes, readPolicy, validateHandoff, listHandoffs, acceptHandoff, rejectHandoff, handoffPrompt, defaultHandoffDir } from '../worker/main.mjs'

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

const jiraBase = { confirm: true, project: 'LIFE', typeId: '10021', title: '  Follow up  ', body: 'hello', labels: ['concord', 'pilot'], parent: 'LIFE-125' }
const noRun = async () => { throw Error('must not run') }

test('jira create refuses bad input and never runs Orca', async () => {
  const bad = [
    [{ confirm: false }, /explicit confirm/], [{ project: 'life' }, /project/], [{ typeId: 'x' }, /issue type/], [{ title: '  ' }, /title/],
    [{ title: 'x'.repeat(256) }, /255/], [{ labels: ['a b'] }, /Labels/], [{ parent: 'LIFE' }, /Parent/], [{ body: 'x'.repeat(20001) }, /20000/]
  ]
  for (const [extra, pattern] of bad) await assert.rejects(createJira({ ...jiraBase, ...extra }, { runWithInput: noRun, sites: async () => '' }), pattern)
})

test('jira create passes fixed argv, body on stdin, trimmed title, and builds the browse url', async () => {
  let seen
  const out = await createJira(jiraBase, { runWithInput: async (cmd, args, input) => { seen = { args, input }; return JSON.stringify({ ok: true, result: { key: 'LIFE-999' } }) }, sites: async () => 'https://example.atlassian.net' })
  assert.deepEqual(out, { key: 'LIFE-999', url: 'https://example.atlassian.net/browse/LIFE-999' })
  assert.deepEqual(seen.args.slice(0, 11), ['jira', 'create', '--project', 'LIFE', '--type', '10021', '--title', 'Follow up', '--body-file', '-', '--json'])
  assert.deepEqual(JSON.parse(seen.args.at(-1)), { labels: ['concord', 'pilot'], parent: { key: 'LIFE-125' } })
  assert.equal(seen.input, 'hello')
})

test('jira create fails closed when Orca returns no issue key, and shows what it returned', async () => {
  await assert.rejects(createJira(jiraBase, { runWithInput: async () => 'weird output', sites: async () => '' }), /check Jira before retrying.*weird output/)
  const plain = await createJira(jiraBase, { runWithInput: async () => 'Created LIFE-1000', sites: async () => '' })
  assert.equal(plain.key, 'LIFE-1000')
})

test('jira project and type lists are filtered', async () => {
  assert.deepEqual(await jiraProjects(async () => ({ stdout: JSON.stringify({ result: [{ key: 'LIFE', name: 'LIFE OS Development' }, { key: 'bad key', name: 'x' }] }) })), [{ key: 'LIFE', name: 'LIFE OS Development' }])
  assert.deepEqual(await jiraTypes('LIFE', async () => ({ stdout: JSON.stringify({ result: [{ id: '10021', name: 'Task' }, { id: '10099', name: 'Sub-task', subtask: true }, { id: 'zz', name: 'x' }] }) })), [{ id: '10021', name: 'Task' }])
  await assert.rejects(jiraTypes('life'), /project/)
})

test('editor tab and sidebar panel share one page and script, with no inline script (inline scripts do not run in the editor frame)', () => {
  const html = readFileSync(new URL('../panel/index.html', import.meta.url), 'utf8')
  assert.match(html, /<script src="avatars\.js"><\/script>/)
  assert.match(html, /<script src="room\.js"><\/script>/)
  assert.equal(/<script(?![^>]*\bsrc=)[^>]*>/u.test(html), false)
  const loader = readFileSync(new URL('../editor/main.ts', import.meta.url), 'utf8')
  assert.match(loader, /panel\/index\.html/); assert.match(loader, /panel\/room\.js/); assert.match(loader, /panel\/avatars\.js/)
  const manifest = JSON.parse(readFileSync(new URL('../orca-plugin.json', import.meta.url), 'utf8'))
  assert.equal(manifest.contributes.customEditors[0].entry, 'editor/main.ts')
  assert.deepEqual(manifest.capabilities, [{ kind: 'settings:own' }])
})

test('avatars are small embedded PNGs for the four Buzz identities and the brand colours match Buzz', () => {
  const avatars = readFileSync(new URL('../panel/avatars.js', import.meta.url), 'utf8')
  for (const key of ['claudecode', 'codex', 'claudechat', 'chatgpt']) assert.match(avatars, new RegExp(`"${key}": "data:image/png;base64,`))
  assert.ok(avatars.length < 40000)
  const html = readFileSync(new URL('../panel/index.html', import.meta.url), 'utf8')
  for (const hex of ['#b5532f', '#d97757', '#10a37f', '#5b5fef']) assert.ok(html.includes(hex), hex)
})

const GOOD_POLICY = { senders: ['chatgpt-chat', 'claude-chat'], targets: { agents: true, newChat: ['claude', 'codex'] }, requireAccept: true }
const handoff = (over = {}) => ({ id: 'hf-aaaaaaaa', sender: 'chatgpt-chat', jiraKey: 'LIFE-411', scope: 'Check status', brief: 'SECRET-BRIEF-TEXT read the Jira issue', nonce: 'nonce-aaaaaaaa', createdAt: '2026-11-09T03:00:00Z', target: { kind: 'any' }, ...over })
const hdeps = { jiraStatus: async () => 'To Do' }
async function folder(policy = GOOD_POLICY) {
  const dir = await realpath(await mkdtemp(path.join(tmpdir(), 'concord-handoffs-')))
  if (policy !== null) await writeFile(path.join(dir, 'policy.json'), typeof policy === 'string' ? policy : JSON.stringify(policy))
  return dir
}
const put = (dir, h) => writeFile(path.join(dir, `${h.id}.json`), typeof h === 'string' ? h : JSON.stringify(h))

test('handoffs fail closed without a valid policy', async t => {
  const none = await folder(null)
  const bad1 = await folder({ ...GOOD_POLICY, requireAccept: false })
  const bad2 = await folder({ ...GOOD_POLICY, senders: ['someone-else'] })
  const bad3 = await folder('not json')
  t.after(() => Promise.all([none, bad1, bad2, bad3].map(d => rm(d, { recursive: true, force: true }))))
  for (const d of [none, bad1, bad2, bad3]) await assert.rejects(listHandoffs(d, hdeps), /policy/)
  assert.throws(() => defaultHandoffDir('relative/path'), /absolute/)
})

test('handoff list reports invalid, duplicate and non-plain files instead of trusting them', async t => {
  const dir = await folder()
  t.after(() => rm(dir, { recursive: true, force: true }))
  await put(dir, handoff())
  await put(dir, handoff({ id: 'hf-bbbbbbbb', nonce: 'nonce-aaaaaaaa', createdAt: '2026-11-09T03:05:00Z' }))
  await put(dir, handoff({ id: 'hf-cccccccc', sender: 'random-bot' }))
  await put(dir, handoff({ id: 'hf-dddddddd', jiraKey: 'nope' }))
  await put(dir, handoff({ id: 'hf-eeeeeeee', target: { kind: 'new-chat', agent: 'rm' } }))
  await writeFile(path.join(dir, 'hf-ffffffff.json'), JSON.stringify(handoff({ id: 'hf-zzzzzzzz', nonce: 'nonce-ffffffff' })))
  await writeFile(path.join(dir, 'hf-gggggggg.json'), '{broken')
  await symlink(path.join(dir, 'hf-aaaaaaaa.json'), path.join(dir, 'hf-hhhhhhhh.json'))
  const { handoffs } = await listHandoffs(dir, hdeps)
  const by = Object.fromEntries(handoffs.map(h => [h.id, h]))
  assert.equal(by['hf-aaaaaaaa'].status, 'pending')
  assert.equal(by['hf-aaaaaaaa'].jiraStatus, 'To Do')
  assert.equal(by['hf-bbbbbbbb'].status, 'duplicate')
  for (const id of ['hf-cccccccc', 'hf-dddddddd', 'hf-eeeeeeee', 'hf-ffffffff', 'hf-gggggggg', 'hf-hhhhhhhh']) assert.equal(by[id].status, 'invalid', id)
  assert.throws(() => validateHandoff(handoff({ id: 'hf-aaaaaaaa' }), 'hf-other-name', { senders: new Set(['chatgpt-chat']), newChat: new Set(), agents: true }), /does not match/)
})

test('accept delivers to an open agent as the relay, with the guard text, and writes state and a text-free audit line', async t => {
  const dir = await folder()
  t.after(() => rm(dir, { recursive: true, force: true }))
  await put(dir, handoff())
  let sent
  const send = async (input) => { sent = input; return { delivery: 'delivered', status: 'sent', messageId: 'msg-123' } }
  const out = await acceptHandoff({ handoffDir: dir, repoRoot: '/r', executable: 'concord', id: 'hf-aaaaaaaa', target: { kind: 'agent', id: 'claude-code:f6958fb5' } }, { ...hdeps, send })
  assert.equal(out.status, 'delivered')
  assert.equal((await listHandoffs(dir, hdeps)).handoffs.find(h => h.id === 'hf-aaaaaaaa').messageId, 'msg-123')
  assert.equal(sent.senderId, 'relay:orca-room')
  assert.equal(sent.to, 'claude-code:f6958fb5')
  assert.match(sent.content, /pointer, not an order/)
  assert.match(sent.content, /LIFE-411/)
  assert.match(sent.content, /REFUSED/)
  const audit = await readF(path.join(dir, 'audit.log'), 'utf8')
  assert.match(audit, /"op":"accept"/); assert.match(audit, /"outcome":"delivered"/)
  assert.equal(audit.includes('SECRET-BRIEF-TEXT'), false)
  await assert.rejects(acceptHandoff({ handoffDir: dir, repoRoot: '/r', executable: 'concord', id: 'hf-aaaaaaaa', target: { kind: 'agent', id: 'claude-code:f6958fb5' } }, { ...hdeps, send }), /already delivered/)
})

test('accept reports queued, not delivered, when Concord only queued it', async t => {
  const dir = await folder()
  t.after(() => rm(dir, { recursive: true, force: true }))
  await put(dir, handoff())
  const out = await acceptHandoff({ handoffDir: dir, repoRoot: '/r', executable: 'concord', id: 'hf-aaaaaaaa', target: { kind: 'agent', id: 'codex:23408389' } }, { ...hdeps, send: async () => ({ delivery: 'queued_pull', status: 'sent' }) })
  assert.equal(out.status, 'queued')
  assert.match(out.detail, /not delivered yet/)
})

test('a handoff that names its target cannot be redirected, and "any" needs a choice', async t => {
  const dir = await folder()
  t.after(() => rm(dir, { recursive: true, force: true }))
  await put(dir, handoff({ target: { kind: 'agent', id: 'claude-code:f6958fb5' } }))
  await assert.rejects(acceptHandoff({ handoffDir: dir, repoRoot: '/r', executable: 'concord', id: 'hf-aaaaaaaa', target: { kind: 'agent', id: 'codex:23408389' } }, { ...hdeps, send: async () => { throw Error('must not send') } }), /cannot be redirected/)
  await put(dir, handoff({ id: 'hf-bbbbbbbb', nonce: 'nonce-bbbbbbbb' }))
  await assert.rejects(acceptHandoff({ handoffDir: dir, repoRoot: '/r', executable: 'concord', id: 'hf-bbbbbbbb', target: {} }, { ...hdeps, send: async () => { throw Error('must not send') } }), /Choose where/)
})

test('new chat launches only in the configured repo and only for kinds the policy allows', async t => {
  const dir = await folder({ ...GOOD_POLICY, targets: { agents: true, newChat: ['claude'] } })
  const repo = await realpath(await mkdtemp(path.join(tmpdir(), 'concord-repo-')))
  await mkdir(path.join(repo, '.concord'))
  t.after(() => Promise.all([dir, repo].map(d => rm(d, { recursive: true, force: true }))))
  await put(dir, handoff())
  let seen
  const launch = async (a) => { seen = a; return 'new claude chat x (prompt journaled)' }
  await assert.rejects(acceptHandoff({ handoffDir: dir, repoRoot: repo, executable: 'concord', id: 'hf-aaaaaaaa', target: { kind: 'new-chat', agent: 'codex' } }, { ...hdeps, launch }), /does not allow/)
  assert.equal(seen, undefined)
  const out = await acceptHandoff({ handoffDir: dir, repoRoot: repo, executable: 'concord', id: 'hf-aaaaaaaa', target: { kind: 'new-chat', agent: 'claude' } }, { ...hdeps, launch })
  assert.equal(out.status, 'started')
  assert.equal(seen.agent, 'claude'); assert.equal(seen.worktreeRoot, repo); assert.match(seen.prompt, /pointer, not an order/)
  await assert.rejects(acceptHandoff({ handoffDir: dir, repoRoot: '/nonexistent', executable: 'concord', id: 'hf-aaaaaaaa', target: { kind: 'new-chat', agent: 'claude' } }, { ...hdeps, launch }), /already started|does not exist/)
})

test('reject records state, audits, and blocks any later accept', async t => {
  const dir = await folder()
  t.after(() => rm(dir, { recursive: true, force: true }))
  await put(dir, handoff())
  assert.equal((await rejectHandoff({ handoffDir: dir, id: 'hf-aaaaaaaa' }, hdeps)).status, 'rejected')
  await assert.rejects(rejectHandoff({ handoffDir: dir, id: 'hf-aaaaaaaa' }, hdeps), /already rejected/)
  await assert.rejects(acceptHandoff({ handoffDir: dir, repoRoot: '/r', executable: 'concord', id: 'hf-aaaaaaaa', target: { kind: 'agent', id: 'claude-code:f6958fb5' } }, { ...hdeps, send: async () => { throw Error('must not send') } }), /already rejected/)
  assert.match(await readF(path.join(dir, 'audit.log'), 'utf8'), /"op":"reject"/)
  assert.match(handoffPrompt({ id: 'hf-1', sender: 'claude-chat', jiraKey: 'LIFE-1', scope: 's', brief: 'b' }), /Claude Chat/)
})
