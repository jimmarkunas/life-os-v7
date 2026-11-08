// LIFE OS probe (LIFE-401, AL-0b). Observe only: every hook returns undefined, so nothing is blocked, changed or decided.
// It answers three questions about the INSTALLED Buzz build before any enforcement is written:
//   1. does a PreToolUse hook fire for each harness, and with which event name and tool-name field;
//   2. does the hook's context name the calling agent (context.agent) and session;
//   3. what does client.pubkey / context look like inside an added tool.
// Observations hold names only (agent key, harness, event, tool name): never tool input, never output, never prompt text.
export const inject = ["agents"];

const HOOKS = [
  ["buzz.claude-code/claude-code", "PreToolUse"],
  ["buzz.codex/codex", "PreToolUse"],
  ["buzz.pi/pi", "beforeTool"],
];
const MAX = 50;

export function toolName(input) {
  if (!input || typeof input !== "object") return null;
  const name = input.tool_name ?? (input.call && input.call.name) ?? input.name ?? null;
  return typeof name === "string" ? name.slice(0, 120) : null;
}

export function record(seen, harness, event, input, context) {
  seen.push({
    t: new Date().toISOString(),
    harness,
    event,
    agent: context && typeof context.agent === "string" ? context.agent : null,
    session: context && typeof context.session === "string" ? context.session.slice(0, 16) : null,
    tool: toolName(input),
  });
  while (seen.length > MAX) seen.shift();
}

export function apply(ctx) {
  const seen = [];
  const errors = [];
  for (const [harness, event] of HOOKS) {
    try {
      ctx.agents.registerHook({
        id: "probe-" + harness.replace(/[^a-z0-9]+/gi, "-"),
        harness,
        event,
        run(input, context) {
          try {
            record(seen, harness, event, input, context);
          } catch (e) {
            errors.push(String(e && e.message ? e.message : e).slice(0, 120));
          }
          return undefined; // observe only
        },
      });
    } catch (e) {
      errors.push(harness + ": " + String(e && e.message ? e.message : e).slice(0, 120));
    }
  }
  ctx.agents.addTool({
    name: "lifeos_probe_report",
    description: "LIFE OS probe: returns the last 50 tool calls this plugin saw (agent key, harness, event, tool name) and who the calling agent is. Read only.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    async call(client, context) {
      return JSON.stringify({
        you: { pubkey: client && client.pubkey ? client.pubkey : null, key: client && client.key ? client.key : null },
        context: context ? { channel: context.channel ?? null, root: context.root ?? null, topLevel: context.topLevel ?? null, wholeChannel: context.wholeChannel ?? null } : null,
        hooks_registered: HOOKS.length - errors.filter((e) => e.includes(":")).length,
        errors,
        seen,
      });
    },
  });
}
