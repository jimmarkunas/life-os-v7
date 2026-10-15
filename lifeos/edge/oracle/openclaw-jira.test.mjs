// node --test lifeos/edge/oracle/openclaw-jira.test.mjs   (synthetic issue keys and dates only; no network)
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { propose, confirm, reject, PROPOSAL_TTL_MIN } from "./openclaw-jira.mjs";

const NOW = Date.parse("2026-10-15T20:00:00Z");
const CODE = "ABCDEFGHJK";

function jira(due = "2026-10-16", { failPut = false, readBack = null } = {}) {
  const state = { due, puts: 0 };
  state.call = async (method, p, opts = {}) => {
    if (method === "GET") return { key: "TEST-123", fields: { duedate: state.due, summary: "Synthetic" } };
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
  assert.deepEqual(s.sent[0].b.map((b) => [b.label, b.action.value]), [["Approve", `lifeos-approve:${CODE}`], ["Reject", `lifeos-reject:${CODE}`]]);
  assert.ok(!fs.readdirSync(s.dir).some((f) => f.includes(CODE)));                      // only a hash of the code is stored
  assert.equal(j.puts, 0);
});

test("an approved change executes once, reads back, and a replay does nothing", async () => {
  const s = setup(); const j = jira();
  await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
  const done = await confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW + 60000, notify: s.notify });
  assert.deepEqual([done.executed, done.due, j.puts], [true, "2026-10-19", 1]);
  assert.match(s.sent.at(-1).t, /Done: TEST-123 due date is now Oct 19, 2026 \(read back/);
  await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW + 70000, notify: s.notify }), { code: "NOT_FOUND" });
  assert.equal(j.puts, 1);
  assert.deepEqual(fs.readdirSync(s.approvalDir).filter((f) => !f.includes(".used.")), []);   // the permit was consumed
});

test("reject, an unknown code, and an expired approval execute nothing", async () => {
  const s = setup(); const j = jira();
  await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
  assert.equal(reject(CODE, { dir: s.dir, now: NOW, notify: s.notify }).rejected, true);
  await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW, notify: s.notify }), { code: "NOT_FOUND" });
  await assert.rejects(confirm("ZZZZZZZZZZ", { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW, notify: s.notify }), { code: "NOT_FOUND" });
  await assert.rejects(confirm("not-a-code", { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW, notify: s.notify }), { code: "NOT_FOUND" });
  const late = setup();
  await propose(proposeArgs, { call: j.call, dir: late.dir, now: NOW, notify: late.notify, code: CODE });
  await assert.rejects(confirm(CODE, { call: j.call, dir: late.dir, approvalDir: late.approvalDir, now: NOW + PROPOSAL_TTL_MIN * 60000 + 1, notify: late.notify }), { code: "EXPIRED" });
  assert.equal(j.puts, 0);
});

test("an edited proposal is refused and a different change needs its own approval", async () => {
  const s = setup(); const j = jira();
  await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
  const [file] = fs.readdirSync(s.dir).map((f) => path.join(s.dir, f));
  const rec = JSON.parse(fs.readFileSync(file, "utf8"));
  rec.op.fields.duedate = "2026-12-31";                                                   // edited after approval was requested
  fs.writeFileSync(file, JSON.stringify(rec), { mode: 0o600 });
  await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW, notify: s.notify }), { code: "TAMPERED" });
  assert.equal(j.puts, 0);
});

test("a change to the issue since the preview blocks the stale approval", async () => {
  const s = setup(); const j = jira();
  await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
  j.due = "2026-10-17";                                                                  // someone moved it meanwhile
  await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW, notify: s.notify }), { code: "BASELINE_CHANGED" });
  assert.equal(j.puts, 0);
  assert.match(s.sent.at(-1).t, /Not applied/);
});

test("a failed write or a wrong read-back is never reported as success", async () => {
  for (const [opts, code] of [[{ failPut: true }, "WRITE_FAILED"], [{ readBack: "2026-10-20" }, "READBACK_MISMATCH"]]) {
    const s = setup(); const j = jira("2026-10-16", opts);
    await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: s.notify, code: CODE });
    await assert.rejects(confirm(CODE, { call: j.call, dir: s.dir, approvalDir: s.approvalDir, now: NOW, notify: s.notify }), { code });
    assert.ok(!s.sent.some((m) => m.t.startsWith("✅")));
  }
});

test("a failed Telegram delivery grants nothing; the demo never touches Jira", async () => {
  const s = setup(); const j = jira();
  const out = await propose(proposeArgs, { call: j.call, dir: s.dir, now: NOW, notify: () => false, code: CODE });
  assert.equal(out.delivered, false);
  const d = setup(); const none = { call: async () => { throw new Error("Jira must not be called"); } };
  await propose(["update", "TEST-123", "--due", "2026-10-19", "--demo", "--demo-current", "2026-10-16"], { call: none.call, dir: d.dir, now: NOW, notify: d.notify, code: CODE });
  const r = await confirm(CODE, { call: none.call, dir: d.dir, approvalDir: d.approvalDir, now: NOW, notify: d.notify });
  assert.deepEqual([r.demo, r.executed], [true, false]);
  assert.match(d.sent.at(-1).t, /Nothing was changed in Jira/);
});
