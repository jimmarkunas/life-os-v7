// node --test lifeos/edge/oracle/openclaw-jira.test.mjs   (synthetic issue keys and dates only; no network)
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { propose, confirm, reject, PROPOSAL_TTL_MIN } from "./openclaw-jira.mjs";

const NOW = Date.parse("2026-10-15T20:00:00Z");
const CODE = "ABCDEFGHJK";

function jira(due = "2026-10-16", { failPut = false, readBack = null, status = "To Do" } = {}) {
  const state = { due, status, puts: 0 };
  state.call = async (method, p, opts = {}) => {
    if (method === "GET" && p.endsWith("/transitions")) return { transitions: [{ id: "11", name: "To Do", to: { name: "To Do" } }, { id: "31", name: "Done", to: { name: "Done" } }] };
    if (method === "GET") return { key: "TEST-123", fields: { duedate: state.due, status: { name: state.status }, summary: "Synthetic" } };
    if (method === "POST" && p.endsWith("/transitions")) {
      state.puts += 1;
      state.status = opts.body.transition.id === "31" ? "Done" : "To Do";
      return {};
    }
    if (method === "PUT") {
      state.puts += 1;
      if (failPut) throw new Error("HTTP 500");
      state.due = readBack ?? opts.body.fields.duedate;
      return {};
    }
    throw new Error(`unexpected ${method} ${p}`);
  };
  return state;
}

function setup() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "jira-chat-"));
  const sent = [];
  return { dir: path.join(root, "proposals"), approvalDir: path.join(root, "approvals"), sent, notify: (t, b) => (sent.push({ t, b }), true) };
}

const proposeArgs = ["update", "TEST-123", "--due", "2026-10-19"];

test("the preview names the target, both values, expiry and link, with Approve/Reject; the code is never returned", async () => {
  const s = setup(); const j = jira();
  const out = await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
  assert.deepEqual([out.key, out.from, out.to, out.delivered], ["TEST-123", "2026-10-16", "2026-10-19", true]);
  assert.ok(!JSON.stringify(out).includes(CODE));
  const msg = s.sent[0].t;
  for (const part of ["TEST-123", "Oct 16, 2026", "Oct 19, 2026", "Expires", "browse/TEST-123"]) assert.ok(msg.includes(part), part);
  assert.deepEqual(s.sent[0].b.map((b) => [b.label, b.action, b.value]), [["Approve", undefined, `lifeos-approve:${CODE}`], ["Reject", undefined, `lifeos-reject:${CODE}`]]);   // no `action`: OpenClaw would make it opaque
  assert.ok(!fs.readdirSync(s.dir).some((f) => f.includes(CODE)));                      // only a hash of the code is stored
  assert.equal(j.puts, 0);
});

test("an approved change executes once, reads back, and a replay does nothing", async () => {
  const s = setup(); const j = jira();
  await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
  const done = await confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW + 60000 });
  assert.deepEqual([done.executed, done.due, j.puts], [true, "2026-10-19", 1]);
  assert.match(done.message, /Done: TEST-123 due date is now Oct 19, 2026 \(read back/);
  await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW + 70000 }), { code: "NOT_FOUND" });
  assert.equal(j.puts, 1);
  assert.deepEqual(fs.readdirSync(s.approvalDir).filter((f) => !f.includes(".used.")), []);   // the permit was consumed
});

test("reject, an unknown code, and an expired approval execute nothing", async () => {
  const s = setup(); const j = jira();
  await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
  assert.equal(reject(CODE, { dir: s.dir, now: NOW }).rejected, true);
  await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW }), { code: "NOT_FOUND" });
  await assert.rejects(confirm("ZZZZZZZZZZ", { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW }), { code: "NOT_FOUND" });
  await assert.rejects(confirm("not-a-code", { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW }), { code: "NOT_FOUND" });
  const late = setup();
  await propose(proposeArgs, { call: j.call, dir: late.dir, now: NOW, notify: late.notify, code: CODE });
  await assert.rejects(confirm(CODE, { call: j.call, dir: late.dir, approvalDir: late.approvalDir, now: NOW + PROPOSAL_TTL_MIN * 60000 + 1 }), { code: "EXPIRED" });
  assert.equal(j.puts, 0);
});

test("an edited proposal is refused and a different change needs its own approval", async () => {
  const s = setup(); const j = jira();
  await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
  const [file] = fs.readdirSync(s.dir).map((f) => path.join(s.dir, f));
  const rec = JSON.parse(fs.readFileSync(file, "utf8"));
  rec.op.fields.duedate = "2026-12-31";                                                   // edited after approval was requested
  fs.writeFileSync(file, JSON.stringify(rec), { mode: 0o600 });
  await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW }), { code: "TAMPERED" });
  assert.equal(j.puts, 0);
});

test("a change to the issue since the preview blocks the stale approval", async () => {
  const s = setup(); const j = jira();
  await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
  j.due = "2026-10-17";                                                                  // someone moved it meanwhile
  await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW }), (e) => e.code === "BASELINE_CHANGED" && /^❌ Not applied/.test(e.extra.message));
  assert.equal(j.puts, 0);
});

test("a failed write or a wrong read-back is never reported as success", async () => {
  for (const [opts, code] of [[{ failPut: true }, "WRITE_FAILED"], [{ readBack: "2026-10-20" }, "READBACK_MISMATCH"]]) {
    const s = setup(); const j = jira("2026-10-16", opts);
    await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
    await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW }), (e) => e.code === code && e.extra.message.startsWith("❌"));
  }
});

test("a failed Telegram delivery grants nothing; the demo never touches Jira", async () => {
  const s = setup(); const j = jira();
  const out = await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: () => false, code: CODE });
  assert.equal(out.delivered, false);
  const d = setup(); const none = { call: async () => { throw new Error("Jira must not be called"); } };
  await propose(["update", "TEST-123", "--due", "2026-10-19", "--demo", "--demo-current", "2026-10-16"], { call: none.call, dir: d.dir, now: NOW, notify: d.notify, code: CODE });
  const r = await confirm(CODE, { call: none.call, dir: d.dir, approvalDir: d.approvalDir, now: NOW });
  assert.deepEqual([r.demo, r.executed], [true, false]);
  assert.match(r.message, /Nothing was changed in Jira/);
  assert.equal(d.sent.length, 1);                                                        // only the preview; the result is Luna's one reply
});

test("mark Done: preview shows the status change, executes once through the transition, and reads back Done", async () => {
  const s = setup(); const j = jira();
  const out = await propose(["transition", "TEST-123", "--to", "Done"], { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
  assert.deepEqual([out.field, out.from, out.to], ["status", "To Do", "Done"]);
  assert.ok(s.sent[0].t.includes("Jira TEST-123: mark Done") && s.sent[0].t.includes("Current: To Do"));
  const done = await confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW });
  assert.deepEqual([done.executed, done.status, j.puts], [true, "Done", 1]);
  assert.match(done.message, /TEST-123 status is now Done \(read back/);
  await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW }), { code: "NOT_FOUND" });
  assert.equal(j.puts, 1);
});

test("mark Done refuses other targets, an already-Done issue, and a status changed since the preview", async () => {
  const s = setup(); const j = jira();
  for (const bad of [["transition", "TEST-123", "--to", "In Progress"], ["transition", "TEST-123", "--to", "done"], ["transition", "TEST-123"]]) {
    await assert.rejects(propose(bad, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE }), { code: "USAGE" });
  }
  await assert.rejects(propose(["transition", "TEST-123", "--to", "Done"], { call: jira("2026-10-16", { status: "Done" }).call, dir: s.dir, now: NOW, notify: s.notify, code: CODE }), { code: "NO_CHANGE" });
  await propose(["transition", "TEST-123", "--to", "Done"], { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
  j.status = "In Progress";
  await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW }), (e) => e.code === "BASELINE_CHANGED" && /^❌ Not applied: TEST-123 status/.test(e.extra.message));
  assert.equal(j.puts, 0);
});
