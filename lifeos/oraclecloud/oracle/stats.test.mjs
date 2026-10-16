// node --test lifeos/oraclecloud/oracle/stats.test.mjs   (synthetic inputs; no network, no OCI)
import test from "node:test";
import assert from "node:assert/strict";
import { parsePlan, reclaimState, memoryPct, diskPct } from "./stats.mjs";

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
