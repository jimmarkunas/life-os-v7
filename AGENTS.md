# LIFE OS V7: agent rules (one page)

The [Development Policy](docs/life-os-development-policy.md) is the whole rule set (one file). Code map and commands: [docs/agent-reference.md](docs/agent-reference.md).

## The job

Jim starts every run; no scheduler, timer or loop starts an agent. Once started, run end to end:

1. **Pick** the ticket Jim named, else the top `Agent Go` leaf by Priority, then Rank. Skip In Progress tickets (another session has them). Agent Go is Jim's approval for everything in the ticket: never re-ask.
2. **Claim:** transition 14 to In Progress. No comment.
3. **Build** on `life/LIFE-nnn/<slug>`, one PR `[LIFE-nnn] <outcome>`. Never change a ticket's scope without asking Jim.
4. **Test:** `sh tools/preflight.sh` before every push; CI is the full gate.
5. **Land and close:** auto-merge on green, then `python -m lifeos.jira.closeout KEY... --pr N --sha S --run R --proof KEY=text --live`. Then the next ticket (`NO_ELIGIBLE_WORK` if none).

## Jim must do first

Every ticket lists what only Jim can do (secret, permission, sign-in, spend, host step, decision, Notion wireframe). An unchecked item or a missing section keeps it out of Agent Go. A need found mid-run: add it to that section, label `needs-jim`, one comment, keep building the rest.

## Always

- Stops for Jim: wider workflow `permissions`, a new or moved credential (Bitwarden only), deleting GitHub data, spend over budget.
- Public repo: no personal data or secrets, ever (docs/PRIVACY.md).
- Jira connector: `mcp__claude_ai_Atlassian_MCP__*` only; Rovo connectors are retired. Pass `fields` on reads.
- Times for Jim: Central Time, 12-hour, labeled CT.
- Routing: [docs/index.json](docs/index.json).

<!-- concord:start -->
## Concord — shared work-state for coding agents

<!-- concord:workflow-version=7 -->

This project uses Concord MCP. Keep coordination to the five workflow tools:

- **Before editing**, call `start_work` with the task, your agent kind, and
  expected files or modules. It registers presence, accepts assigned work when
  appropriate, claims the scope, and returns overlap warnings. Concord derives
  your `agent_id` from your session — omit it unless your client told you one.
- Use `inspect_work` to read the workspace, one task, one agent, or one message
  thread. Use `update_work` for durable progress and for live prompts/replies
  to another promptable workspace agent, including while that agent is busy.
- Use `transfer_work` for assignment, acceptance, decline, release,
  reassignment, evidence-bearing handoff offers, and reopening.
- **Before finishing**, call `finish_work` once with the outcome, changed
  files, tests, assumptions, decisions, risks, guardrails, and provenance. It
  records evidence and can mark work review-ready or terminal.
- Before handing work to cloud Devin, use `transfer_work` to hand off or
  release your local claim; cloud Devin cannot access this local Concord state.

Keep each claim small and resolve reported overlaps before editing. Concord
regenerates human-readable review artifacts in `.concord/`.

Enforcement remains client-dependent. `concord doctor` reports setup and
workflow adoption; optional hooks can block exact-file collisions.

<!-- concord:end -->