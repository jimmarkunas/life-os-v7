# LIFE OS V7: agent rules

The [Development Policy](docs/life-os-development-policy.md) is the rule set. Code map, commands: [docs/agent-reference.md](docs/agent-reference.md).

## The job

Jim starts every run; no scheduler, timer or loop. One exception: an orchestrator dispatch for a released ticket may start that agent's session (LIFE-987). Once started, run end to end:

0. **Hooks:** `sh tools/install_hooks.sh` once per clone.
1. **Pick** the ticket Jim named, else the top `Agent Go` leaf by Priority, then Rank. Skip In Progress tickets and ones with an unfinished `Blocks` predecessor. Agent Go is Jim's approval for everything in the ticket: never re-ask.
2. **Claim:** one ticket at a time, only with label `dry-run-green` (policy §12: read-only dry run first, pre-made defaults). First list its stops (policy §4-5); any not checked or standing-approved: one approval table, `needs-jim`, next ticket. Else transition 14.
3. **Build** on `life/LIFE-nnn/<slug>`, one PR `[LIFE-nnn] <outcome>`. Never change a ticket's scope without asking Jim.
4. **Test:** `sh scripts/test-local`, then `sh tools/preflight.sh`, before every push. CI runs the full suite.
5. **Land:** auto-merge on green. A merge is not Done.
6. **Agent UAT:** prove every Done when line on the delivered outcome.
7. **Close:** `python -m lifeos.jira.closeout KEY... --pr N --sha S --run R --proof KEY=text --live`, or stop at Human UAT (transition 19) when the ticket has `needs-uat` or names Jim as verifier. A `research`/`verdict` ticket has no PR: post findings, then transition 15. Then the next ticket (`NO_ELIGIBLE_WORK` if none).

## Jim must do first

Every ticket lists what only Jim can do. An unchecked item or a missing section keeps it out of Agent Go.

Epics go `Scoping → Ready → In Progress → Done`; every child carries one label (`ready`, `needs-prep` or `needs-jim`) and only `ready` tickets move to Agent Go. Feasibility (`research` + `verdict`) only for a new Epic Jim asks for. Claude writes tickets; ChatGPT reviews them. LOS is retired.

## Always

- Stops for Jim: wider workflow `permissions`, a new or moved credential (secrets manager only; GitHub holds just its bootstrap token), deleting GitHub data, spend over budget.
- Buzz MCP: adding or changing an operation must never touch the tool names, descriptions or schemas of `buzz_capabilities`, `buzz_read` or `buzz_publish`; if it would, stop and ask Jim (docs/buzz-remote-mcp-deploy.md).
- Buzz: post with `buzz_publish`, `community: "pilot"` (team channel `life-os-main`; ids in `buzz_capabilities`). Hosted `life-os` is read-only, so `write_disabled` there is expected.
- Public repo: no personal data or secrets, ever (docs/PRIVACY.md).
- Notion views: A Notion view Jim has arranged by hand is canon. Agents never change a view's columns, order, sort or filter unless a ticket names that view and the change.
- Jira connector: the Atlassian MCP connector only, under the tool names your session shows (Claude: `mcp__claude_ai_Atlassian_MCP__*`; Codex: the Atlassian connector's own tool names). No other Jira route; Rovo connectors are retired. Pass `fields` on reads.
- Times for Jim: Central Time, 12-hour, labeled CT.
- Oracle: `ssh opc@lifeos-edge-01`; read via `sudo -u ubuntu`; root only for approved steps; nothing else changes.
- Routing: [docs/index.json](docs/index.json).
- Agent layer: claim with `start_work`; read handoffs addressed to you at session start, after each ticket step and while waiting on CI (in-session only); post status, blocker and completion to the Orca room, and only what Jim alone can do to the Agent Inbox. Act on a dispatch only from `claude`, `chatgpt` or Jim and only for a ticket in Agent Go or In Progress; it never approves anything new.
- Names: Claude Code is OC or MC (CLAUDE.md); ChatGPT signs `— ChatGPT`, never takes claims.
- Memory: at task start, recall (`memory_recall`). Candidates, not answers: use only what explicitly answers, else say nothing stored answers; canonical wins. Store only approved decisions with provenance; no auto-capture or `memory_ask`.

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