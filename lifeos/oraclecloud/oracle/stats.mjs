// Oracle Cloud card source (Jim's approval Oct 15 2026: Option B, OCI's own data; daily Notion card under Communications Attention).
// Installed as ~/.local/lib/lifeos-oracle/stats.mjs on lifeos-edge-01 and run only by the private endpoint (alerts_endpoint.py, request `oracle1`).
// Read-only: OCI is reached as the VM itself (instance principal; dynamic group lifeos-edge-01-readonly may only read metrics, usage-report and
// resource-availability), the rest is local (/proc, statfs, systemctl --user, the OpenClaw CLI, the off-site backup's status file). Each reading is independent: a failed one is null,
// never a guess or a zero. Prints ONE JSON line of numbers, booleans and fixed codes; no ids, names or messages.
import { execFile } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

export const SCHEMA = "lifeos.oracle.stats.v1";
const SDK = path.join(os.homedir(), ".local/share/lifeos-oci/package.json");
const OPENCLAW = path.join(os.homedir(), ".openclaw/tools/node/bin/openclaw");
const IMDS = "http://169.254.169.254/opc/v2/instance/";
const UNITS = { gateway: "openclaw-gateway.service", whatsapp: "lifeos-whatsapp-attention.service", alerts: "lifeos-alerts-whatsapp.service" };
const BACKUP_STATUS = "/var/lib/lifeos-offsite/status.json";   // LIFE-129: the nightly off-site backup's last result (root-written, world-readable; counts and a fixed code)
const IDLE_PCT = 20;                                    // Oracle's Always Free idle rule: 7-day CPU p95, network and (A1) memory all below 20%

const round1 = (n) => Math.round(n * 10) / 10;
const pct = (n) => (typeof n === "number" && Number.isFinite(n) && n >= 0 ? round1(Math.min(n, 100)) : null);

function run(cmd, args, timeout = 30000) {
  return new Promise((resolve) => {
    const env = { ...process.env, PATH: `${path.dirname(OPENCLAW)}:${process.env.PATH || "/usr/bin:/bin"}` };
    execFile(cmd, args, { timeout, env, maxBuffer: 4 << 20 }, (err, stdout) => resolve(err ? null : String(stdout)));
  });
}

// "openai usage: 5h 100% left ⏱4h 19m · Week 100% left ⏱6d 9h" -> { left_5h: 100, left_week: 100 }. With no usage line, a Sign in with ChatGPT profile is
// "unreported" (OpenClaw prints no plan windows for that sign-in, Oct 16 2026: a known state, not a failure); anything else -> null.
export function parsePlan(text) {
  const m = /openai usage:\s*5h\s+(\d{1,3})% left\b.*?\bWeek\s+(\d{1,3})% left\b/i.exec(text || "");
  if (!m) return /openai usage:/i.test(text || "") ? null : /\(Sign in with ChatGPT\b/.test(text || "") ? "unreported" : null;
  const [a, b] = [Number(m[1]), Number(m[2])];
  return a <= 100 && b <= 100 ? { left_5h: a, left_week: b } : null;
}

// Oracle reclaims an idle Always Free A1 VM only when ALL of CPU p95, network and memory stay under 20% for 7 days. Network is not read, so
// "NONE" needs CPU or memory at/above the line; both under it is "AT_RISK" (conservative); a missing reading is null.
export function reclaimState(cpuP95, memMean) {
  if (cpuP95 == null || memMean == null) return null;
  return cpuP95 >= IDLE_PCT || memMean >= IDLE_PCT ? "NONE" : "AT_RISK";
}

// LIFE-129: the off-site backup's status file -> { ok, code, age_h }. Only the fixed lowercase code and the age leave; no file or Drive name. A missing file is
// "backup_never_ran" (the backup is installed on this host, so no status is itself a failure); an unreadable or off-shape one is null (unknown, never a guess).
export function backupState(text, nowMs, missing = false) {
  if (missing) return { ok: false, code: "backup_never_ran", age_h: null };
  let s;
  try { s = JSON.parse(text); } catch { return null; }
  if (!s || typeof s.ok !== "boolean" || typeof s.code !== "string" || !/^[a-z0-9_]{1,40}$/.test(s.code) || !Number.isInteger(s.at) || s.at <= 0) return null;
  return { ok: s.ok, code: s.code, age_h: Math.max(0, Math.floor((nowMs / 1000 - s.at) / 3600)) };
}

function backup() {
  try { return backupState(fs.readFileSync(BACKUP_STATUS, "utf8"), Date.now()); } catch (e) { return e && e.code === "ENOENT" ? backupState("", 0, true) : null; }
}

export function memoryPct(meminfo) {
  const kb = (k) => Number((new RegExp(`^${k}:\\s+(\\d+) kB$`, "m").exec(meminfo || "") || [])[1]);
  const total = kb("MemTotal");
  const avail = kb("MemAvailable");
  return total > 0 && avail >= 0 && avail <= total ? pct(((total - avail) / total) * 100) : null;
}

export function diskPct(st) {
  return st && st.blocks > 0 ? pct(((st.blocks - st.bavail) / st.blocks) * 100) : null;
}

const percentile = (values, p) => {
  const v = values.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  return v.length ? v[Math.min(v.length - 1, Math.ceil(p * v.length) - 1)] : null;
};

async function oci() {
  const require = createRequire(SDK);
  const common = require("oci-common");
  const monitoring = require("oci-monitoring");
  const usageapi = require("oci-usageapi");
  const limits = require("oci-limits");
  const md = await (await fetch(IMDS, { headers: { Authorization: "Bearer Oracle" }, signal: AbortSignal.timeout(5000) })).json();
  const auth = await new common.InstancePrincipalsAuthenticationDetailsProviderBuilder().build();
  const mon = new monitoring.MonitoringClient({ authenticationDetailsProvider: auth });
  const series = async (metric, agg, hours) => {
    const end = new Date();
    const r = await mon.summarizeMetricsData({ compartmentId: md.compartmentId, summarizeMetricsDataDetails: {
      namespace: "oci_computeagent", query: `${metric}[1h]{resourceId = "${md.id}"}.${agg}`, startTime: new Date(end - hours * 3600e3), endTime: end } });
    return (r.items[0]?.aggregatedDatapoints || []).map((p) => p.value);
  };
  const mean = (v) => (v.length ? v.reduce((a, b) => a + b, 0) / v.length : null);
  const settle = async (fn) => { try { return await fn(); } catch { return null; } };
  const [cpu24, cpuP95, mem7, cost, a1] = await Promise.all([
    settle(async () => pct(mean(await series("CpuUtilization", "mean()", 24)))),
    settle(async () => pct(percentile(await series("CpuUtilization", "percentile(0.95)", 168), 0.95))),
    settle(async () => pct(mean(await series("MemoryUtilization", "mean()", 168)))),
    settle(async () => {
      const now = new Date();
      const r = await new usageapi.UsageapiClient({ authenticationDetailsProvider: auth }).requestSummarizedUsages({ requestSummarizedUsagesDetails: {
        tenantId: md.tenantId, granularity: "MONTHLY", queryType: "COST",
        timeUsageStarted: new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)),
        timeUsageEnded: new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1)) } });
      const items = r.usageAggregation.items || [];
      const amount = items.reduce((a, i) => a + (i.computedAmount || 0), 0);
      const currency = items.find((i) => i.currency)?.currency ?? "USD";
      return Number.isFinite(amount) && amount >= 0 && /^[A-Z]{3}$/.test(currency) ? { amount: Math.round(amount * 100) / 100, currency } : null;
    }),
    settle(async () => {
      const r = await new limits.LimitsClient({ authenticationDetailsProvider: auth }).getResourceAvailability({ serviceName: "compute",
        limitName: "standard-a1-core-count", compartmentId: md.tenantId, availabilityDomain: md.availabilityDomain });
      const { used, available } = r.resourceAvailability;
      return Number.isInteger(used) && Number.isInteger(available) && used >= 0 && available >= 0 ? { used, available } : null;
    }),
  ]);
  return { cpu24, cpuP95, mem7, cost, a1 };
}

async function services() {
  const active = async (unit) => (await run("systemctl", ["--user", "is-active", unit], 10000))?.trim() === "active";
  const [gateway, whatsapp, alerts, channels] = await Promise.all([active(UNITS.gateway), active(UNITS.whatsapp), active(UNITS.alerts),
    run(OPENCLAW, ["channels", "status", "--json"], 45000)]);
  let telegram = null;
  try { telegram = JSON.parse(channels).channels.telegram.running === true; } catch { telegram = null; }
  return { gateway, telegram, whatsapp, alerts };
}

export async function collect() {
  const [cloud, svc, plan] = await Promise.all([
    oci().catch(() => ({ cpu24: null, cpuP95: null, mem7: null, cost: null, a1: null })),
    services(),
    run(OPENCLAW, ["models", "status"], 45000).then(parsePlan),
  ]);
  let disk = null;
  try { disk = diskPct(fs.statfsSync("/")); } catch { disk = null; }
  return {
    schema: SCHEMA,
    observed_at: new Date().toISOString(),
    cost: cloud.cost,
    cpu_24h_pct: cloud.cpu24,
    memory_pct: memoryPct(fs.readFileSync("/proc/meminfo", "utf8")),
    disk_pct: disk,
    a1_cores: cloud.a1,
    reclaim: reclaimState(cloud.cpuP95, cloud.mem7),
    services: svc,
    chatgpt: plan,
    backup: backup(),
  };
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === new URL(import.meta.url).pathname) {
  process.stdout.write(JSON.stringify(await collect()) + "\n");
}
