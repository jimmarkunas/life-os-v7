#!/home/ubuntu/.openclaw/tools/node/bin/node
// Narrow Jira capability for the OpenClaw edge assistant (EDGE-1.4).
//
// Fixed site, fixed REST routes, no arbitrary HTTP. Credentials are resolved at call time through
// OpenClaw's own `bws` SecretRef provider script; nothing is stored, copied or printed.
//
//   read  : whoami | projects | get KEY | transitions KEY | search JQL [--max N]
//   write : create | update | comment | transition      (each needs a single-use human approval)
//   admin : approve <write-subcommand ...>               (NOT in the Gateway exec allowlist)
//   chat  : propose update KEY --due DATE [--demo --demo-current DATE] | confirm CODE | reject CODE
//           (Jim approves in Telegram: see "chat approval" below; the code is never shown to the assistant)
//
// Never implemented: delete, bulk, sprint/board changes, assignee/permission/admin, arbitrary URLs.
import { execFileSync, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const SITE = "https://jimmarkunas.atlassian.net";
const RESOLVER = "/home/ubuntu/.local/bin/openclaw-bws-resolver.mjs";
const ENV_FILE = path.join(os.homedir(), ".config/bws/openclaw.env");
export const APPROVAL_DIR = path.join(os.homedir(), ".openclaw/jira-approvals");
const AUDIT_FILE = path.join(os.homedir(), ".openclaw/logs/jira-audit.jsonl");
const EMAIL_ID = "openclaw/jira/email";
const TOKEN_IDS = ["openclaw/jira/apiToken", "LIFEOS-OpenClaw-Jira"]; // canonical name first, vault's current name second
const DEFAULT_TTL_MIN = 15;
const MAX_TTL_MIN = 60;

const KEY_RE = /^[A-Z][A-Z0-9]{1,9}-[0-9]{1,7}$/;
const PROJECT_RE = /^[A-Z][A-Z0-9]{1,9}$/;
const LABEL_RE = /^[A-Za-z0-9_.-]{1,50}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const PRIORITIES = new Set(["Highest", "High", "Medium", "Low", "Lowest"]);
const ISSUE_FIELDS = "summary,status,issuetype,priority,assignee,labels,duedate,created,updated,project,parent,description";
const SEARCH_FIELDS = "summary,status,issuetype,priority,assignee,labels,duedate,updated,project";

// The only routes this process may ever call.
const ROUTES = [
  ["GET", /^\/rest\/api\/3\/myself$/],
  ["GET", /^\/rest\/api\/3\/project\/search$/],
  ["GET", /^\/rest\/api\/3\/search\/jql$/],
  ["GET", /^\/rest\/api\/3\/issue\/[A-Z][A-Z0-9]{1,9}-[0-9]{1,7}$/],
  ["GET", /^\/rest\/api\/3\/issue\/[A-Z][A-Z0-9]{1,9}-[0-9]{1,7}\/transitions$/],
  ["POST", /^\/rest\/api\/3\/issue$/],
  ["PUT", /^\/rest\/api\/3\/issue\/[A-Z][A-Z0-9]{1,9}-[0-9]{1,7}$/],
  ["POST", /^\/rest\/api\/3\/issue\/[A-Z][A-Z0-9]{1,9}-[0-9]{1,7}\/comment$/],
  ["POST", /^\/rest\/api\/3\/issue\/[A-Z][A-Z0-9]{1,9}-[0-9]{1,7}\/transitions$/],
];

export class CliError extends Error {
  constructor(code, detail, exit = 2, extra = {}) {
    super(code);
    this.code = code;
    this.detail = detail;
    this.exit = exit;
    this.extra = extra;
  }
}

export function loadCredentials(env = process.env) {
  let token = env.BWS_ACCESS_TOKEN;
  let bin = env.BWS_BIN;
  let server = env.BWS_SERVER_URL;
  if (!token) {
    try {
      for (const line of fs.readFileSync(ENV_FILE, "utf8").split("\n")) {
        const m = /^\s*(BWS_ACCESS_TOKEN|BWS_BIN|BWS_SERVER_URL)=(.*)$/.exec(line);
        if (!m) continue;
        const v = m[2].trim().replace(/^(['"])(.*)\1$/, "$2");
        if (m[1] === "BWS_ACCESS_TOKEN") token = v;
        else if (m[1] === "BWS_BIN") bin = bin || v;
        else server = server || v;
      }
    } catch {
      /* fall through to CREDENTIAL_UNAVAILABLE */
    }
  }
  if (!token) throw new CliError("CREDENTIAL_UNAVAILABLE", "vault access is not configured for this process", 4);
  let out;
  try {
    out = execFileSync(RESOLVER, [], {
      input: JSON.stringify({ protocolVersion: 1, provider: "bws", ids: [EMAIL_ID, ...TOKEN_IDS] }),
      encoding: "utf8",
      timeout: 20000,
      env: {
        PATH: env.PATH || "/usr/bin:/bin",
        BWS_ACCESS_TOKEN: token,
        ...(bin ? { BWS_BIN: bin } : {}),
        ...(server ? { BWS_SERVER_URL: server } : {}),
      },
      stdio: ["pipe", "pipe", "ignore"],
    });
  } catch {
    throw new CliError("CREDENTIAL_UNAVAILABLE", "SecretRef provider failed", 4);
  }
  let values = {};
  try {
    values = JSON.parse(out).values || {};
  } catch {
    /* handled below */
  }
  const email = values[EMAIL_ID];
  const apiToken = TOKEN_IDS.map((id) => values[id]).find(Boolean);
  if (!email || !apiToken) {
    const missing = [!email && EMAIL_ID, !apiToken && TOKEN_IDS[0]].filter(Boolean);
    throw new CliError("CREDENTIAL_UNAVAILABLE", `SecretRef(s) did not resolve: ${missing.join(", ")}`, 4);
  }
  return { email, apiToken };
}

function routeAllowed(method, pathname) {
  return ROUTES.some(([m, re]) => m === method && re.test(pathname));
}

export function makeClient({ fetchImpl = globalThis.fetch, creds }) {
  const auth = "Basic " + Buffer.from(`${creds.email}:${creds.apiToken}`).toString("base64");
  return async function call(method, pathname, { query, body } = {}) {
    if (!routeAllowed(method, pathname)) throw new CliError("ROUTE_NOT_ALLOWED", `${method} ${pathname}`, 2);
    const url = new URL(SITE + pathname);
    for (const [k, v] of Object.entries(query || {})) url.searchParams.set(k, String(v));
    const res = await fetchImpl(url, {
      method,
      redirect: "error",
      headers: {
        Authorization: auth,
        Accept: "application/json",
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401 || res.status === 403) {
      throw new CliError("JIRA_AUTH_FAILED", `HTTP ${res.status} from Jira (credential rejected or lacks permission)`, 4, { status: res.status });
    }
    if (res.status === 204) return {};
    let data = {};
    const text = await res.text();
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = {};
    }
    if (!res.ok) {
      const msg = [...(data.errorMessages || []), ...Object.values(data.errors || {})].join("; ").slice(0, 300);
      const hint = res.status === 404 ? " [Jira returns 404 both for a missing issue and for a rejected/anonymous or unauthorized credential; run whoami to tell them apart]" : "";
      throw new CliError("JIRA_HTTP_ERROR", `HTTP ${res.status}${msg ? ": " + msg : ""}${hint}`, 5, { status: res.status });
    }
    return data;
  };
}

function adfToText(n) {
  if (n == null) return "";
  if (typeof n === "string") return n;
  if (n.type === "text") return n.text || "";
  if (n.type === "hardBreak") return "\n";
  const inner = (n.content || []).map(adfToText).join("");
  return ["paragraph", "heading", "listItem", "codeBlock", "blockquote"].includes(n.type) ? inner + "\n" : inner;
}

function textToAdf(text) {
  const paras = String(text).split(/\n{2,}/).filter((p) => p.trim().length);
  return {
    type: "doc",
    version: 1,
    content: (paras.length ? paras : [""]).map((p) => ({ type: "paragraph", content: p ? [{ type: "text", text: p }] : [] })),
  };
}

export function shapeIssue(issue, { full = true } = {}) {
  const f = issue.fields || {};
  const out = {
    key: issue.key,
    url: `${SITE}/browse/${issue.key}`,
    project: f.project?.key ?? null,
    summary: f.summary ?? null,
    type: f.issuetype?.name ?? null,
    status: f.status?.name ?? null,
    statusCategory: f.status?.statusCategory?.name ?? null,
    priority: f.priority?.name ?? null,
    assignee: f.assignee?.displayName ?? null,
    labels: f.labels ?? [],
    due: f.duedate ?? null,
    updated: f.updated ?? null,
  };
  if (full) {
    out.created = f.created ?? null;
    out.parent = f.parent?.key ?? null;
    out.description = adfToText(f.description).trim().slice(0, 1500);
  }
  return out;
}

// ---- argument parsing -------------------------------------------------------------------------
const WRITE_FLAGS = {
  create: ["--project", "--type", "--summary", "--description", "--labels", "--priority", "--due"],
  update: ["--summary", "--description", "--labels", "--priority", "--due"],
};

function parseFlags(tokens, allowed) {
  const flags = {};
  let current = null;
  for (const t of tokens) {
    if (t.startsWith("--")) {
      if (!allowed.includes(t)) throw new CliError("USAGE", `unknown flag ${t.slice(0, 20)}`);
      if (t in flags) throw new CliError("USAGE", `duplicate flag ${t}`);
      current = t;
      flags[t] = [];
    } else {
      if (!current) throw new CliError("USAGE", "unexpected positional value");
      flags[current].push(t);
    }
  }
  const out = {};
  for (const [k, v] of Object.entries(flags)) {
    if (!v.length) throw new CliError("USAGE", `${k} needs a value`);
    out[k.slice(2)] = v.join(" ");
  }
  return out;
}

function requireKey(k) {
  if (!KEY_RE.test(k || "")) throw new CliError("USAGE", "issue key must look like ABC-123");
  return k;
}

function cleanLine(name, v, max) {
  if (typeof v !== "string" || !v.trim() || v.length > max || /[\u0000-\u001f]/.test(v)) {
    throw new CliError("USAGE", `${name} must be 1-${max} chars, single line`);
  }
  return v.trim();
}

function cleanText(name, v, max) {
  if (typeof v !== "string" || !v.trim() || v.length > max) throw new CliError("USAGE", `${name} must be 1-${max} chars`);
  return v;
}

function fieldValues(f) {
  const out = {};
  if (f.summary !== undefined) out.summary = cleanLine("summary", f.summary, 255);
  if (f.description !== undefined) out.description = cleanText("description", f.description, 8000);
  if (f.labels !== undefined) {
    const labels = f.labels.split(",").map((s) => s.trim()).filter(Boolean);
    if (!labels.length || labels.length > 10 || !labels.every((l) => LABEL_RE.test(l))) throw new CliError("USAGE", "labels: up to 10 of [A-Za-z0-9_.-]");
    out.labels = labels;
  }
  if (f.priority !== undefined) {
    if (!PRIORITIES.has(f.priority)) throw new CliError("USAGE", "priority must be Highest|High|Medium|Low|Lowest");
    out.priority = f.priority;
  }
  if (f.due !== undefined) {
    if (!DATE_RE.test(f.due)) throw new CliError("USAGE", "due must be YYYY-MM-DD");
    out.duedate = f.due;
  }
  return out;
}

// Parse a write subcommand into a canonical, hashable operation.
export function parseWrite(sub, tokens) {
  if (sub === "create") {
    const f = parseFlags(tokens, WRITE_FLAGS.create);
    if (!PROJECT_RE.test(f.project || "")) throw new CliError("USAGE", "--project must be a project key like JFM");
    const type = cleanLine("type", f.type || "Task", 40);
    if (f.summary === undefined) throw new CliError("USAGE", "--summary is required");
    const { project: _p, type: _t, ...rest } = f;
    return { action: "create", project: f.project, type, fields: fieldValues(rest) };
  }
  if (sub === "update") {
    const key = requireKey(tokens[0]);
    const f = parseFlags(tokens.slice(1), WRITE_FLAGS.update);
    const fields = fieldValues(f);
    if (!Object.keys(fields).length) throw new CliError("USAGE", "update needs at least one field flag");
    return { action: "update", key, fields };
  }
  if (sub === "comment") {
    const key = requireKey(tokens[0]);
    return { action: "comment", key, body: cleanText("comment", tokens.slice(1).join(" "), 4000) };
  }
  if (sub === "transition") {
    const key = requireKey(tokens[0]);
    return { action: "transition", key, to: cleanLine("target status", tokens.slice(1).join(" "), 60) };
  }
  throw new CliError("USAGE", "unknown subcommand");
}

function canonical(v) {
  if (Array.isArray(v)) return v.map(canonical);
  if (v && typeof v === "object") return Object.fromEntries(Object.keys(v).sort().map((k) => [k, canonical(v[k])]));
  return v;
}

export function opHash(op) {
  return createHash("sha256").update(JSON.stringify(canonical(op))).digest("hex");
}

// ---- approval gate (deterministic; the model cannot satisfy it) -------------------------------
export function checkApproval(op, { dir = APPROVAL_DIR, now = Date.now() } = {}) {
  const hash = opHash(op);
  const file = path.join(dir, `${hash}.json`);
  let st;
  try {
    st = fs.statSync(file);
  } catch {
    return { ok: false, hash, reason: "no approval on file for this exact operation" };
  }
  if (!st.isFile() || st.uid !== process.getuid() || (st.mode & 0o022) !== 0) {
    return { ok: false, hash, reason: "approval file has unsafe ownership or permissions" };
  }
  let rec;
  try {
    rec = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return { ok: false, hash, reason: "approval file unreadable" };
  }
  if (rec.opHash !== hash || opHash(rec.op) !== hash) return { ok: false, hash, reason: "approval does not match this operation" };
  if (!(Date.parse(rec.expiresAt) > now)) return { ok: false, hash, reason: "approval expired" };
  return { ok: true, hash, file, rec };
}

export function consumeApproval(file, now = Date.now()) {
  fs.renameSync(file, file.replace(/\.json$/, `.used.${now}.json`)); // single use; fail closed on any error
}

export function writeApproval(op, { dir = APPROVAL_DIR, ttlMin = DEFAULT_TTL_MIN, by = "jim", now = Date.now() } = {}) {
  if (!(ttlMin > 0 && ttlMin <= MAX_TTL_MIN)) throw new CliError("USAGE", `ttl must be 1-${MAX_TTL_MIN} minutes`);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const hash = opHash(op);
  const rec = {
    opHash: hash,
    op,
    approvedBy: String(by).slice(0, 40),
    approvedAt: new Date(now).toISOString(),
    expiresAt: new Date(now + ttlMin * 60000).toISOString(),
    singleUse: true,
  };
  fs.writeFileSync(path.join(dir, `${hash}.json`), JSON.stringify(rec, null, 2), { mode: 0o600 });
  return rec;
}

function audit(entry) {
  try {
    fs.mkdirSync(path.dirname(AUDIT_FILE), { recursive: true, mode: 0o700 });
    try {
      if (fs.statSync(AUDIT_FILE).size > 512 * 1024) fs.renameSync(AUDIT_FILE, AUDIT_FILE + ".1");
    } catch {
      /* no file yet */
    }
    fs.appendFileSync(AUDIT_FILE, JSON.stringify({ ts: new Date().toISOString(), ...entry }) + "\n", { mode: 0o600 });
  } catch {
    /* auditing must never break the call */
  }
}


// ---- chat approval (Telegram buttons -> the same single-use permit) ------------------------------
// Two approvable operations: an issue's due date, and moving an issue to Done.
// propose: reads the issue's CURRENT value, records a pending proposal bound to the exact operation, that baseline, a payload version and a
// 10-minute expiry, and sends Jim's private Telegram chat (the single allowlisted id) a preview with Approve / Reject buttons. The buttons
// carry a random one-time code; only its sha256 is stored and the code is never printed, so the assistant cannot approve on its own.
// confirm/reject: the code arrives back only from Jim's tap (OpenClaw passes `callback_data: ...` from authorized senders). The proposal is
// claimed (renamed) BEFORE anything else, so a code works once; expired, unknown, tampered or already-used codes do nothing. confirm re-reads
// the issue and refuses if the value changed since the preview, then mints the existing permit (1 minute), writes through the same update/transition
// path, reads the value back and returns the verified result for Luna's reply.
export const PROPOSAL_DIR = path.join(APPROVAL_DIR, "proposals");
export const PROPOSAL_TTL_MIN = 10;
export const PAYLOAD_VERSION = 2;
const CODE_RE = /^[A-Z2-9]{10}$/;
const CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
const OPENCLAW_BIN = path.join(os.homedir(), ".openclaw/tools/node/bin/openclaw");
const OPENCLAW_CONFIG = path.join(os.homedir(), ".openclaw/openclaw.json");

export function newCode(bytes = randomBytes(10)) {
  return Array.from(bytes, (b) => CODE_ALPHABET[b % CODE_ALPHABET.length]).join("");
}

const codeHash = (code) => createHash("sha256").update("lifeos-chat-approval:" + code).digest("hex");

function telegramOwner(configPath = OPENCLAW_CONFIG) {
  const allow = ((JSON.parse(fs.readFileSync(configPath, "utf8")).channels || {}).telegram || {}).allowFrom || [];
  return allow.length === 1 && /^\d{5,15}$/.test(String(allow[0])) ? String(allow[0]) : null;
}

export function telegramNotify(text, buttons = null) {
  // Jim's private chat with the LIFE OS bot, through the existing OpenClaw channel. Never throws: delivery failure never authorizes anything.
  try {
    const to = telegramOwner();
    if (!to) return false;
    const args = ["message", "send", "--channel", "telegram", "--target", to, "-m", text];
    if (buttons) args.push("--presentation", JSON.stringify({ blocks: [{ type: "buttons", buttons }] }));
    const env = { ...process.env, PATH: `${path.dirname(OPENCLAW_BIN)}:${process.env.PATH || "/usr/bin:/bin"}` };
    return spawnSync(OPENCLAW_BIN, args, { env, timeout: 90000, stdio: "ignore" }).status === 0;
  } catch {
    return false;
  }
}

const ctDate = (d) => (d ? new Date(`${d}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }) : "none");
const ctTime = (ms) => new Date(ms).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" }) + " CT";

// The only operations Jim can approve from chat. Each names how to read the issue's current value, the approved target, and how to show it.
const PROPOSAL_USAGE = "propose supports: update KEY --due YYYY-MM-DD [--demo --demo-current YYYY-MM-DD] | transition KEY --to Done";
const KINDS = {
  due: { action: "update", title: "change due date", what: "due date", current: (issue) => issue.due ?? null, target: (op) => op.fields.duedate, show: ctDate,
    same: (a, b) => a === b },
  done: { action: "transition", title: "mark Done", what: "status", current: (issue) => issue.status ?? null, target: (op) => op.to, show: (v) => v ?? "none",
    same: (a, b) => String(a ?? "").toLowerCase() === String(b ?? "").toLowerCase() },
};

export function parseProposal(tokens) {
  const key = requireKey(tokens[1]);
  if (tokens[0] === "transition") {
    if (tokens.length !== 4 || tokens[2] !== "--to" || tokens[3] !== "Done") throw new CliError("USAGE", PROPOSAL_USAGE);
    return { kind: "done", op: parseWrite("transition", [key, "Done"]), demo: false, demoCurrent: null };
  }
  if (tokens[0] !== "update") throw new CliError("USAGE", PROPOSAL_USAGE);
  const flags = {};
  for (let i = 2; i < tokens.length; i += 1) {
    const t = tokens[i];
    if (t === "--demo") flags.demo = true;
    else if ((t === "--due" || t === "--demo-current") && DATE_RE.test(tokens[i + 1] || "")) flags[t.slice(2)] = tokens[++i];
    else throw new CliError("USAGE", PROPOSAL_USAGE);
  }
  if (!flags.due || Boolean(flags.demo) !== Boolean(flags["demo-current"])) throw new CliError("USAGE", "--due is required; --demo needs --demo-current");
  return { kind: "due", op: parseWrite("update", [key, "--due", flags.due]), demo: Boolean(flags.demo), demoCurrent: flags["demo-current"] || null };
}

export async function propose(tokens, { call, dir = PROPOSAL_DIR, now = Date.now(), notify = telegramNotify, code = newCode() } = {}) {
  const { kind, op, demo, demoCurrent } = parseProposal(tokens);
  const k = KINDS[kind];
  const current = demo ? demoCurrent : k.current(await readIssue(call, op.key, false));
  if (k.same(current, k.target(op))) throw new CliError("NO_CHANGE", `the proposed ${k.what} equals the current one`);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const rec = { version: PAYLOAD_VERSION, kind, op, opHash: opHash(op), baseline: current, demo,
    createdAt: new Date(now).toISOString(), expiresAt: new Date(now + PROPOSAL_TTL_MIN * 60000).toISOString() };
  fs.writeFileSync(path.join(dir, `${codeHash(code)}.json`), JSON.stringify(rec), { mode: 0o600, flag: "wx" });
  const lines = [
    `🟡 LIFE OS · approval needed${demo ? " (SYNTHETIC DEMO: approving changes nothing)" : ""}`,
    `Jira ${op.key}: ${k.title}`,
    `Current: ${k.show(current)}`,
    `Proposed: ${k.show(k.target(op))}`,
    `Expires ${ctTime(now + PROPOSAL_TTL_MIN * 60000)} · works once`,
    `${SITE}/browse/${op.key}`,
  ];
  // Plain `value` buttons, not `action` buttons: OpenClaw wraps `action` values as opaque tgcb1: data that only a
  // registered plugin handler can claim, so the tap never reaches Luna ("This action is no longer available").
  const sent = notify(lines.join("\n"), [
    { label: "Approve", value: `lifeos-approve:${code}`, style: "success" },
    { label: "Reject", value: `lifeos-reject:${code}`, style: "danger" },
  ]);
  return { ok: true, proposed: true, delivered: sent, demo, key: op.key, field: kind === "due" ? "duedate" : "status", from: current, to: k.target(op), expiresAt: rec.expiresAt };
}

export function claimProposal(code, { dir = PROPOSAL_DIR, now = Date.now(), as = "claimed" } = {}) {
  // Single use: the proposal file is renamed before it is read or acted on; a second claim of the same code finds nothing.
  if (!CODE_RE.test(String(code || ""))) throw new CliError("NOT_FOUND", "no pending proposal for that code");
  const file = path.join(dir, `${codeHash(code)}.json`);
  const claimed = file.replace(/\.json$/, `.${as}.${now}.json`);
  try {
    fs.renameSync(file, claimed);
  } catch {
    throw new CliError("NOT_FOUND", "no pending proposal for that code (unknown, already used or rejected)");
  }
  const st = fs.statSync(claimed);
  if (!st.isFile() || st.uid !== process.getuid() || (st.mode & 0o077) !== 0) throw new CliError("TAMPERED", "proposal file has unsafe ownership or permissions");
  const rec = JSON.parse(fs.readFileSync(claimed, "utf8"));
  const k = KINDS[rec.kind];
  if (rec.version !== PAYLOAD_VERSION || !k || opHash(rec.op) !== rec.opHash || rec.op.action !== k.action || !KEY_RE.test(rec.op.key)
    || (rec.kind === "done" && rec.op.to !== "Done") || (rec.demo && rec.kind !== "due")) {
    throw new CliError("TAMPERED", "proposal does not match its payload version or operation");
  }
  if (!(Date.parse(rec.expiresAt) > now)) throw new CliError("EXPIRED", "the approval window has closed; ask for a fresh proposal");
  return rec;
}

// confirm/reject run inside Luna's turn for the button tap, and OpenClaw requires that turn to answer, so the
// wrapper does not message Jim itself: it returns the exact `message` (also on failure) for Luna to send as her reply.
export async function confirm(code, { call, dir = PROPOSAL_DIR, approvalDir = APPROVAL_DIR, now = Date.now() } = {}) {
  let rec;
  try {
    rec = claimProposal(code, { dir, now });
    const k = KINDS[rec.kind];
    const target = k.target(rec.op);
    if (rec.demo) {
      const message = `✅ Approved (synthetic demo): ${rec.op.key} ${k.what} ${k.show(rec.baseline)} → ${k.show(target)}. Nothing was changed in Jira.`;
      return { ok: true, demo: true, executed: false, key: rec.op.key, message };
    }
    if (!k.same(k.current(await readIssue(call, rec.op.key, false)), rec.baseline)) {
      throw new CliError("BASELINE_CHANGED", "the issue changed since the preview; ask for a fresh proposal");
    }
    writeApproval(rec.op, { dir: approvalDir, ttlMin: 1, by: "jim-telegram", now });
    const gate = checkApproval(rec.op, { dir: approvalDir, now });
    if (!gate.ok) throw new CliError("APPROVAL_REQUIRED", gate.reason);
    consumeApproval(gate.file, now);
    const result = await execWrite(call, rec.op);
    const after = k.current(result.issue || {});
    if (!k.same(after, target)) throw new CliError("READBACK_MISMATCH", `Jira did not read back the approved ${k.what}`);
    const message = `✅ Done: ${rec.op.key} ${k.what} is now ${k.show(after)} (read back from Jira).\n${SITE}/browse/${rec.op.key}`;
    return { ok: true, executed: true, key: rec.op.key, [rec.kind === "due" ? "due" : "status"]: after, opHash: rec.opHash, message };
  } catch (e) {
    const what = rec ? `${rec.op.key} ${KINDS[rec.kind]?.what ?? "change"}` : "this approval";
    if (e instanceof CliError) throw new CliError(e.code, e.detail, e.exit, { ...e.extra, message: `❌ Not applied: ${what} — ${e.detail}.` });
    throw new CliError("WRITE_FAILED", "the Jira write did not complete", 1, { message: `❌ Failed: ${what}. Jira was not confirmed as changed.` });
  }
}

export function reject(code, { dir = PROPOSAL_DIR, now = Date.now() } = {}) {
  try {
    const rec = claimProposal(code, { dir, now, as: "rejected" });
    const k = KINDS[rec.kind];
    return { ok: true, rejected: true, key: rec.op.key, message: `Rejected: ${rec.op.key} ${k.what} stays ${k.show(rec.baseline)}. Nothing was changed.` };
  } catch (e) {
    throw new CliError(e.code, e.detail, e.exit, { ...e.extra, message: `❌ Not rejected — ${e.detail}.` });
  }
}

// ---- operations -------------------------------------------------------------------------------
async function readIssue(call, key, full = true) {
  const issue = await call("GET", `/rest/api/3/issue/${key}`, { query: { fields: ISSUE_FIELDS } });
  return shapeIssue(issue, { full });
}

async function execWrite(call, op) {
  if (op.action === "create") {
    const fields = { project: { key: op.project }, issuetype: { name: op.type }, summary: op.fields.summary };
    if (op.fields.description !== undefined) fields.description = textToAdf(op.fields.description);
    if (op.fields.labels) fields.labels = op.fields.labels;
    if (op.fields.priority) fields.priority = { name: op.fields.priority };
    if (op.fields.duedate) fields.duedate = op.fields.duedate;
    const made = await call("POST", "/rest/api/3/issue", { body: { fields } });
    return { created: made.key, issue: await readIssue(call, made.key) };
  }
  if (op.action === "update") {
    const fields = { ...op.fields };
    if (fields.description !== undefined) fields.description = textToAdf(fields.description);
    if (fields.priority) fields.priority = { name: fields.priority };
    await call("PUT", `/rest/api/3/issue/${op.key}`, { body: { fields } });
    return { updated: op.key, issue: await readIssue(call, op.key) };
  }
  if (op.action === "comment") {
    const made = await call("POST", `/rest/api/3/issue/${op.key}/comment`, { body: { body: textToAdf(op.body) } });
    return { commented: op.key, commentId: made.id ?? null };
  }
  if (op.action === "transition") {
    const list = await call("GET", `/rest/api/3/issue/${op.key}/transitions`);
    const want = op.to.toLowerCase();
    const t = (list.transitions || []).find((x) => x.id === op.to || String(x.name).toLowerCase() === want || String(x.to?.name).toLowerCase() === want);
    if (!t) {
      throw new CliError("TRANSITION_NOT_AVAILABLE", `available: ${(list.transitions || []).map((x) => x.name).join(", ") || "none"}`, 5);
    }
    await call("POST", `/rest/api/3/issue/${op.key}/transitions`, { body: { transition: { id: t.id } } });
    return { transitioned: op.key, issue: await readIssue(call, op.key, false) };
  }
  throw new CliError("USAGE", "unknown write");
}

export async function run(argv, deps = {}) {
  const sub = argv[0];
  const rest = argv.slice(1);
  const getCreds = deps.loadCredentials || loadCredentials;
  const approvalDir = deps.approvalDir || APPROVAL_DIR;
  const now = deps.now || Date.now();
  const connect = () => makeClient({ fetchImpl: deps.fetch, creds: getCreds() });

  if (sub === "whoami") {
    if (rest.length) throw new CliError("USAGE", "usage: whoami");
    const me = await connect()("GET", "/rest/api/3/myself");
    return { ok: true, authenticated: true, accountType: me.accountType ?? null, active: me.active ?? null };
  }
  if (sub === "projects") {
    const call = connect();
    const out = [];
    let startAt = 0;
    for (let page = 0; page < 5; page += 1) {
      const r = await call("GET", "/rest/api/3/project/search", { query: { maxResults: 50, startAt, orderBy: "key" } });
      for (const p of r.values || []) out.push({ key: p.key, name: p.name, type: p.projectTypeKey });
      if (r.isLast !== false || !(r.values || []).length) break;
      startAt += (r.values || []).length;
    }
    return { ok: true, count: out.length, projects: out };
  }
  if (sub === "get") {
    const key = requireKey(rest[0]);
    if (rest.length !== 1) throw new CliError("USAGE", "usage: get KEY");
    return { ok: true, issue: await readIssue(connect(), key) };
  }
  if (sub === "transitions") {
    const key = requireKey(rest[0]);
    if (rest.length !== 1) throw new CliError("USAGE", "usage: transitions KEY");
    const r = await connect()("GET", `/rest/api/3/issue/${key}/transitions`);
    return { ok: true, key, transitions: (r.transitions || []).map((t) => ({ id: t.id, name: t.name, to: t.to?.name ?? null })) };
  }
  if (sub === "search") {
    let max = 10;
    let tokens = rest;
    const i = tokens.lastIndexOf("--max");
    if (i >= 0) {
      max = Number(tokens[i + 1]);
      if (!Number.isInteger(max) || max < 1 || max > 25 || tokens.length !== i + 2) throw new CliError("USAGE", "--max must be 1-25 and last");
      tokens = tokens.slice(0, i);
    }
    const jql = tokens.join(" ").trim();
    if (!jql || jql.length > 500 || /[\u0000-\u001f]/.test(jql)) throw new CliError("USAGE", "JQL must be 1-500 chars, single line");
    const r = await connect()("GET", "/rest/api/3/search/jql", { query: { jql, maxResults: max, fields: SEARCH_FIELDS } });
    return { ok: true, count: (r.issues || []).length, hasMore: Boolean(r.nextPageToken), issues: (r.issues || []).map((x) => shapeIssue(x, { full: false })) };
  }
  if (sub === "create" || sub === "update" || sub === "comment" || sub === "transition") {
    const op = parseWrite(sub, rest);
    const gate = checkApproval(op, { dir: approvalDir, now });
    if (!gate.ok) {
      throw new CliError("APPROVAL_REQUIRED", gate.reason, 3, {
        opHash: gate.hash,
        operation: op,
        note: "Consequential Jira write. Nothing was sent. Jim must approve this exact operation out of band; the assistant cannot approve it.",
      });
    }
    consumeApproval(gate.file, now);
    const result = await execWrite(connect(), op);
    return { ok: true, approvedBy: gate.rec.approvedBy, opHash: gate.hash, ...result };
  }
  const lazyCall = (...a) => connect()(...a);                                   // a demo proposal never touches Jira or its credentials
  if (sub === "propose") return propose(rest, { call: lazyCall, dir: deps.proposalDir, now, notify: deps.notify });
  if (sub === "confirm" && rest.length === 1) {
    return confirm(rest[0], { call: lazyCall, dir: deps.proposalDir, approvalDir, now });
  }
  if (sub === "reject" && rest.length === 1) return reject(rest[0], { dir: deps.proposalDir, now });
  if (sub === "approve") {
    // Operator-only. Not in the exec allowlist, so the assistant cannot reach it.
    const wsub = rest[0];
    let tokens = rest.slice(1);
    let ttlMin = DEFAULT_TTL_MIN;
    const ti = tokens.lastIndexOf("--ttl-min");
    if (ti >= 0) {
      ttlMin = Number(tokens[ti + 1]);
      tokens = tokens.slice(0, ti);
    }
    const op = parseWrite(wsub, tokens);
    const rec = writeApproval(op, { dir: approvalDir, ttlMin, now });
    return { ok: true, approved: true, opHash: rec.opHash, expiresAt: rec.expiresAt, operation: op };
  }
  throw new CliError("USAGE", "subcommands: whoami | projects | get KEY | transitions KEY | search JQL [--max N] | create | update | comment | transition");
}

async function main() {
  const argv = process.argv.slice(2);
  let result;
  let exit = 0;
  try {
    result = await run(argv);
  } catch (e) {
    if (e instanceof CliError) {
      result = { ok: false, error: e.code, detail: e.detail, ...e.extra };
      exit = e.exit;
    } else {
      result = { ok: false, error: "INTERNAL", detail: String(e?.message || e).slice(0, 200) };
      exit = 1;
    }
  }
  const sub = argv[0];
  const pi = argv.indexOf("--project");
  const target = argv.find((a) => KEY_RE.test(a)) || (pi >= 0 ? argv[pi + 1] : null) || null;
  if (sub !== "approve" || result.ok) {
    audit({ sub, target: target && String(target).slice(0, 20), ok: Boolean(result.ok), error: result.error || null, opHash: result.opHash || null });
  }
  process.stdout.write(JSON.stringify(result) + "\n");
  process.exit(exit);
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
