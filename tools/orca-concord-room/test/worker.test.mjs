import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, realpath, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { getMessages, validateRecords, validateExecutable } from '../worker/main.mjs'

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
