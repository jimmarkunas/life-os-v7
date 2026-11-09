---
trigger: always_on
description: Concord multi-agent coordination workflow
---

<!-- concord:start -->
## Concord — shared work-state for coding agents

<!-- concord:workflow-version=7 -->

This project uses Concord MCP. Keep coordination to the five workflow tools:

- **Before editing**, call `start_work` with the task, your agent kind (`antigravity`), and
  expected files or modules. Lifecycle hooks (`.agents/hooks.json`) register your session
  and inject your `agent_id` into context — pass this `agent_id` on your calls.
- Use `inspect_work` to read the workspace, one task, one agent, or one message
  thread. Use `update_work` for durable progress and for live prompts/replies
  to another promptable workspace agent.
- Incoming messages are delivered into a running turn via PreInvocation hooks. To hear them
  while idle, keep exactly one background command running for
  `concord inbox watch --agent <agent-id> --provider antigravity --once`; when it completes, answer the
  message and start a fresh one. You can also inspect or drain them manually with
  `concord inbox drain --provider antigravity --agent <agent_id>`.
- Use `transfer_work` for assignment, acceptance, decline, release,
  reassignment, evidence-bearing handoff offers, and reopening.
- **Before finishing**, call `finish_work` once with the outcome, changed
  files, tests, assumptions, decisions, risks, guardrails, and provenance.

Keep each claim small and resolve reported overlaps before editing. Concord
regenerates human-readable review artifacts in `.concord/`.
<!-- concord:end -->
