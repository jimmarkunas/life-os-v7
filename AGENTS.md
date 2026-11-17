# LIFE OS V7 — agent boot guide (one page)

V7 is the active LIFE OS repo (V1, V2 are read-only references). V7 must run with ChatGPT unavailable (D54). The [Development Policy](docs/life-os-development-policy.md) governs; every other rule, command and policy is in [docs/agent-reference.md](docs/agent-reference.md): read only the section a task needs.

## The whole job

1. **Pick** the ticket Jim named, else the top `Agent Go` leaf by Priority, then Rank. Moving a ticket to Agent Go is the approval; an agent may do it unless the ticket names a protected step. One recorded yes from Jim covers all of a ticket's steps: go on, never re-ask. Release no ticket that STOPs on a missing prerequisite. Start with transition 14. Gate: `python -m lifeos.sources.devtools.readiness issue-key LIFE-nnn`.
2. **Build** on one branch `life/LIFE-nnn/<slug>`. Ticket rule: **Every ticket has a parent epic, a Component and a Fix Version; Jira enforces it.** Tickets on the same files may share one PR and one receipt.
3. **Test** with `sh tools/preflight.sh` before every push (quick, ~15 s); CI is the full gate: fix once if red, never rerun the full suite by hand. `sh tools/automerge.sh <pr>` needs its PASS. `git add` new files first.
4. **Land and close:** auto-merge on same-head green CI, then one call: `python -m lifeos.jira.closeout KEY... --pr N --sha S --run R --proof KEY=text --live`. Merged on green CI with local proof IS Done. Then the next ticket (`NO_ELIGIBLE_WORK` if none).

Workflow edits are pre-approved; ask Jim only where `sh tools/preflight.sh` stops: wider `permissions`, new secret or credential, deleting GitHub values or data, spend beyond budget. Record one `[approvals]` line; wait with `[waiting on] Jim: <the one thing>` and label `needs-jim`.

## Always

- Jira connector: `mcp__claude_ai_Atlassian_MCP__*` only; Rovo connectors are retired. Pass `fields` on reads.
- Public repo: no personal data or secrets, ever; use example.com and invented names (docs/PRIVACY.md).
- Times for Jim: Central Time only, from `python -m lifeos.platform.ct_stamp`.
- Counts only in reports; say what you could not verify.
- Blocking is Jira Blocks links only; `needs-jim` is the only workflow-control label.
- New stage: its code and its workflow step; preflight writes the registry rows. No new stage, step, label, status or check that only manages Jira tickets or process unless Jim asks in chat.
- Keep diffs small; no new framework without asking. A PR that adds code pastes `python -m lifeos.platform.net_size` in its Net size section.

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