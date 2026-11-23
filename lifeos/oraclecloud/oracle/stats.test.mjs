// node --test lifeos/oraclecloud/oracle/stats.test.mjs   (synthetic inputs; no network, no OCI)
import test from "node:test";
import assert from "node:assert/strict";
import { parsePlan, reclaimState, memoryPct, diskPct, backupState, workerState, backupSlots, unitTime, honcho, anthropic } from "./stats.mjs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";

test("the ChatGPT plan line is read strictly; any other shape is unavailable", () => {
  assert.deepEqual(parsePlan("- openai usage: 5h 87% left ⏱4h 19m · Week 42% left ⏱6d 9h"), { left_5h: 87, left_week: 42 });
  assert.equal(parsePlan("- openai usage: unavailable"), null);
  assert.equal(parsePlan("openai usage: 5h 140% left · Week 10% left"), null);
  assert.equal(parsePlan(null), null);
});

test("a Sign in with ChatGPT profile with no usage line is unreported; a broken usage line stays unavailable", () => {
  const siwc = "- openai effective=profiles:~/x | profiles=1 (1 oauth, 0 token, 0 api-key) | openai:token-sharing:x (Sign in with ChatGPT (Beta))";
  assert.equal(parsePlan(siwc), "unreported");
  assert.equal(parsePlan(siwc + "\n- openai usage: unavailable"), null);
  assert.deepEqual(parsePlan(siwc + "\n- openai usage: 5h 50% left · Week 70% left"), { left_5h: 50, left_week: 70 });
  assert.equal(parsePlan("- openai effective=profiles | (Codex OAuth)"), null);
});

test("reclaim risk follows Oracle's all-below-20% idle rule, conservatively", () => {
  assert.equal(reclaimState(5.7, 55), "NONE");          // memory keeps the VM out of the idle rule
  assert.equal(reclaimState(25, 10), "NONE");
  assert.equal(reclaimState(5, 10), "AT_RISK");
  assert.equal(reclaimState(null, 55), null);
});

test("memory and disk are percentages of what is actually in use", () => {
  assert.equal(memoryPct("MemTotal:       1000 kB\nMemFree:  10 kB\nMemAvailable:    450 kB\n"), 55);
  assert.equal(memoryPct("garbage"), null);
  assert.equal(diskPct({ blocks: 1000, bavail: 610 }), 39);
  assert.equal(diskPct({ blocks: 0, bavail: 0 }), null);
});

test("the off-site backup reading carries only ok, a fixed code and the age in whole hours", () => {
  const at = 1793206850;
  assert.deepEqual(backupState(JSON.stringify({ ok: true, code: "ok", at, files: 1154, bytes: 10129666 }), (at + 3 * 3600 + 59) * 1000), { ok: true, code: "ok", age_h: 3 });
  assert.deepEqual(backupState(JSON.stringify({ ok: false, code: "backup_drive_http_403", at }), at * 1000), { ok: false, code: "backup_drive_http_403", age_h: 0 });
  assert.deepEqual(backupState("", 0, true), { ok: false, code: "backup_never_ran", age_h: null });
  assert.equal(backupState("not json", 0), null);
  assert.equal(backupState(JSON.stringify({ ok: true, code: "Some /path or name", at }), at * 1000), null);
  assert.equal(backupState(JSON.stringify({ ok: "yes", code: "ok", at }), at * 1000), null);
});

test("the Codex worker count is the gateway's own app-server children; extras beyond the agents are the leak", () => {
  const ps = ["    1 /sbin/init", "68602 node openclaw gateway", "68602 node openclaw-codex app-server", "68602 node openclaw-codex app-server", "69000 codex app-server", "68602 node other-child", "  68602   node openclaw-codex app-server"].join("\n");
  assert.deepEqual(workerState(ps, 68602, ["fit", "fit2", "fit3", "main", "openclaw"]), { count: 3, agents: 5 });
  assert.deepEqual(workerState(ps, 1, ["main"]), { count: 0, agents: 1 });
  assert.equal(workerState(ps, 0, ["main"]), null);
  assert.equal(workerState(ps, NaN, ["main"]), null);
  assert.equal(workerState(null, 68602, ["main"]), null);
  assert.equal(workerState(ps, 68602, []), null);
});

test("the boot-volume backup slots are two whole numbers or unknown (LIFE-808)", () => {
  assert.deepEqual(backupSlots({ used: 5, available: 0 }), { used: 5, available: 0 });
  assert.deepEqual(backupSlots({ used: 4, available: 1, extra: "x" }), { used: 4, available: 1 });
  for (const bad of [null, undefined, {}, { used: 5 }, { used: -1, available: 0 }, { used: 5, available: 0.5 }, { used: "5", available: 0 }]) assert.equal(backupSlots(bad), null, JSON.stringify(bad));
});

test("LIFE-921: a systemctl timestamp becomes an ISO time or null", () => {
  assert.equal(unitTime("Mon 2026-11-23 09:30:27 UTC"), "2026-11-23T09:30:27Z");
  assert.equal(unitTime("n/a"), null);
  assert.equal(unitTime(""), null);
});

const GOOD_METER = { status: 200, body: { ok: true, spent_usd: 0.40692, cap_usd: 0.9, stopped: false, blocked: null } };
const deps = (over = {}) => ({ json: async (u) => (u.includes("8000") ? { status: 200 } : GOOD_METER), mark: () => false, show: async () => "Mon 2026-11-23 09:30:27 UTC\nsuccess\n", ...over });

test("LIFE-921: a healthy Honcho reads healthy, with RESERVED from the meter and the last backup", async () => {
  assert.deepEqual(await honcho(deps()), { health: true, failed: false, exhausted: false, stopped: false, backup: { at: "2026-11-23T09:30:27Z", ok: true }, meter: { cap: 0.9, reserved: 0.40692 } });
});

test("LIFE-921: a FAILED marker, an EXHAUSTED marker and a meter that blocks are each seen; an unreadable meter is unknown, not stopped", async () => {
  const failed = await honcho(deps({ mark: (f) => f.endsWith("FAILED") }));
  assert.deepEqual([failed.failed, failed.backup.ok], [true, false]);
  assert.equal((await honcho(deps({ mark: (f) => f.endsWith("EXHAUSTED") }))).exhausted, true);
  const blocked = await honcho(deps({ json: async (u) => (u.includes("8000") ? { status: 200 } : { status: 200, body: { ...GOOD_METER.body, blocked: "promotional credits ended" } }) }));
  assert.equal(blocked.stopped, true);
  const dark = await honcho(deps({ json: async (u) => { if (u.includes("8000")) return { status: 200 }; throw new Error("down"); } }));
  assert.deepEqual([dark.stopped, dark.meter], [null, null]);
});

test("LIFE-921: Honcho that does not answer is health false; an unreadable marker is null", async () => {
  assert.equal((await honcho(deps({ json: async (u) => { if (u.includes("8000")) throw new Error("refused"); return GOOD_METER; } }))).health, false);
  assert.equal((await honcho(deps({ mark: () => null }))).failed, null);
});

test("LIFE-921: the report socket's one JSON line is passed on only when it is numbers or nulls", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "rep-"));
  const sock = path.join(dir, "r.sock");
  const serve = (line) => new Promise((resolve) => { const s = net.createServer((c) => c.end(line)); s.listen(sock, () => resolve(s)); });
  let s = await serve('{"billed_usd":0.0227,"honcho_usd":0.0227,"as_of":"2026-11-23T19:00:00Z"}\n');
  assert.deepEqual(await anthropic(sock), { billed_usd: 0.0227, honcho_usd: 0.0227, as_of: "2026-11-23T19:00:00Z" });
  await new Promise((r) => s.close(r)); fs.rmSync(sock, { force: true });
  s = await serve('{"billed_usd":"lots"}\n');
  assert.equal(await anthropic(sock), null);
  await new Promise((r) => s.close(r));
  assert.equal(await anthropic(path.join(dir, "missing.sock")), null);
});
