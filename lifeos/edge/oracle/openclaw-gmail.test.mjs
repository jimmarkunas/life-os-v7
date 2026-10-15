// node --test lifeos/edge/oracle/openclaw-gmail.test.mjs   (synthetic addresses and ids only; no network)
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { run } from "./openclaw-gmail.mjs";

function gmail() {
  const calls = [];
  const fetch = async (url, init = {}) => {
    const u = String(url);
    calls.push(`${init.method || "GET"} ${u.replace(/\?.*$/, "")}`);
    const reply = (obj) => ({ ok: true, status: 200, text: async () => JSON.stringify(obj) });
    if (u.startsWith("https://oauth2.googleapis.com/token")) return reply({ access_token: "synthetic" });
    if (u.endsWith("/users/me/profile")) return reply({ emailAddress: "owner@example.com" });
    if (u.endsWith("/users/me/drafts") && init.method === "POST") return reply({ id: "r-1111", message: { id: "18aaaaaaaaaaaaaa", threadId: "18bbbbbbbbbbbbbb" } });
    throw new Error(`unexpected ${init.method} ${u}`);
  };
  return { fetch, calls };
}

const deps = (g) => ({
  fetch: g.fetch,
  loadCredentials: () => ({ clientId: "c", clientSecret: "s", refreshToken: "r" }),
  auditFile: path.join(fs.mkdtempSync(path.join(os.tmpdir(), "gmail-")), "audit.jsonl"),
  approvalDir: fs.mkdtempSync(path.join(os.tmpdir(), "gmail-approvals-")),
});

test("a draft returns draftId, messageId and threadId for the ChatGPT handoff, and never touches send", async () => {
  const g = gmail();
  const out = await run(["draft", "--to", "someone@example.com", "--subject", "Handoff test", "--body", "Synthetic body"], deps(g));
  assert.deepEqual([out.ok, out.drafted, out.draftId, out.messageId, out.threadId], [true, true, "r-1111", "18aaaaaaaaaaaaaa", "18bbbbbbbbbbbbbb"]);
  assert.match(out.note, /Nothing was sent/);
  assert.ok(!JSON.stringify(out).includes("Synthetic body"));                         // the result names the draft, not its content
  assert.ok(!g.calls.some((c) => c.includes("/messages/send") || c.includes("/drafts/send")));
});
