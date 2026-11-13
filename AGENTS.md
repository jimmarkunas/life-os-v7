# LIFE OS V7 — agent guide (read this, not the whole repo)

V7 is the active LIFE OS implementation. V1 (`life-os-automation`) and V2 (`life-os-v2`) are read-only references: extract the smallest proven mechanic only. Core rule (D54): V7 must run correctly with ChatGPT completely unavailable. This guide is a summary; the [Development Policy](docs/life-os-development-policy.md) governs (read only what you need, especially work selection and the Jira-first lifecycle).

## Delivery flow v3 — the whole job (Jim, November 9, 2026)

1. **Pick.** Jim named a ticket → that one. Else the top `Agent Go` leaf ticket in your lane (`lane-*` label) by Priority, then Rank. Moving a ticket to Agent Go IS the approval. Start with transition ID 14; resuming from blocked, UAT, Done or Cancelled uses "Resume work".
2. **Gate.** `python -m lifeos.readiness` (`issue-key LIFE-nnn`, or `issue-mcp FILE KEY` on an MCP read saved unmodified) checks exactly three things: the key is valid, the ticket has a Done-when line (any Given/When/Then row or bullet under "Done when"), and every `Blocks` predecessor is Done. Anything else prints a `warn` and exits 0; exit 2 only on those three.
3. **Build.** One branch `life/LIFE-nnn/<slug>`, one PR, the ticket's fast proof first. No claim file, no preflight; Concord `start_work`/`finish_work` are optional, never a refusal. Protected steps (secrets, production, spend, user data, host changes) wait for their owned human approval.
4. **Land and close.** Merge when same-head CI is green and no protected item is touched. Post one comment: PR link, what changed, how it was verified, what is unverified. Move the ticket to Done yourself; Jim reopens it or files a Bug. Then pick the next ticket until Agent Go is empty (`NO_ELIGIBLE_WORK`).
5. **Optional.** Label `needs-uat`: stop at "Human UAT" with exact steps for Jim; only for new user-facing UI or judgment calls.

**Scoping.** A scoping note is one page, at most 2,000 characters: Outcome, 3 to 5 Done-when checks, Not in scope. The agent cuts execution tickets from it in the same step. One execution ticket is one PR (or one sheet edit), pasteable into Claude Code with no questions: exact targets, steps, done-when. Interview answers and discovery logs go in a linked page or comments, never the description (over 4,000 characters draws a `warn`).

**Ticket truth and identity.** A plan change is an edit to the ticket plus one line `Scope change: <what> — Jim, <time CT>`; the description beats comments. The claiming agent fixes the ticket's text in the session reality changes; a superseded ticket is Cancelled with a line naming its replacement. Add one label (`agent-claude-code`, `agent-hermes`, `agent-chatgpt`) and start Jira comments with `[agent-…]`.

## Where truth lives (one owner each)

| Thing | Owner | Not here |
|---|---|---|
| Ticket status, approval, receipt, acceptance | Jira LIFE | chat, Notion, comments-as-approval |
| Code, tests, CI, this guide, the policy | GitHub V7 | Notion copies |
| Product requirements and decisions | Notion Product Requirements database for detail; Jira LOS for scoping and research | Jira descriptions (link, don't copy) |
| Runtime schedule and secrets | Production Contract, central secrets service | this repo, Jira, chat |

Whoever changes a fact updates its owner in the same step and links it; a copy elsewhere becomes a link.

## Fast path

Read the named LIFE issue first and only the missing authoritative context; reuse unchanged evidence; run independent reads in parallel; verify every consequential write; never reopen discovery or duplicate planning artifacts.

- **Bootstrap target 60 s**: run `START_EPOCH=$(date -u +%s)` first; after the essential reads report `bootstrap_seconds` and the remote-read count. Warm resumes recheck only live Jira status/ownership and the `origin/main` SHA. Jira: exact key and only summary, status, owner, blockers, acceptance (an omitted field is UNVERIFIED). GitHub: main SHA, PR head/base, changed files, then touched files only.
- **Authority order:** Jim's current instruction → Production Contract (runtime/scheduling) → Development Policy (engineering) → LOS (product) → Notion requirements → LIFE issue (delivery) → existing code. On conflict, use the owning authority; stop before mutating.
- **Scope:** LOS outcome → LIFE Epic → Tasks → Sub-tasks. A missing Done-when line or an unfinished `Blocks` predecessor returns `QUEUE_NOT_READY`; other gaps are warnings.
- **Execution gate:** only a named Jim instruction or an issue Jim moved to Agent Go permits work; a Jira comment or agent message alone grants nothing. Keep one writer per surface and protected-operation approval.
- **Closure:** merged on same-head green CI plus local proof (tests, fixture replays, dry runs on fixtures) → Jira update. Never wait for, or start, an hourly, scheduled, on-demand or benchmark run to prove a change; a live look is a non-blocking watch, and a fault it shows is a Bug under LIFE-615. Report `DONE`, `PARTIAL`, `BLOCKED` or `UNVERIFIED` and the next responsible action.
- **No new runtime:** no paid provider, scheduler, orchestrator, sync store, proxy or governance layer without Jim's explicit approval. A Tech Lead handoff can authorize one specified package, not autonomous pull. `docs/MASTER_ROADMAP.md` never selects work. `python -m lifeos.readiness repo` checks the index, links, owners and size limits (CI runs it).
- **Deadline Mode** (Jim states a deadline): follow CLAUDE.md and the policy: keep an absolute `LIFEOS_DEADLINE_EPOCH`, recheck at every milestone, never reset it, claim delivery only after read-back.

## Buzz pilot entrypoint — only for LIFE-216

[docs/buzz-implementation-roadmap-v1.md](docs/buzz-implementation-roadmap-v1.md) is the frozen 25-step contract and LIFE-216 the evidence ledger; LOS-70 owns product decisions. No Agent Go, credentials, paid usage, live integration or deployment authority comes from this pointer.

## OpenClaw / Oracle edge

Before OpenClaw, Oracle, channel, local-model, assistant, meeting-capture or AI-ops work, read `docs/EDGE_GATEWAY_SCOPE.md` and the relevant `docs/MASTER_ROADMAP.md` row.
- OpenClaw is a bounded capability/control plane; V7 and domain owners keep business rules, persistence and scheduling. No second scheduler or duplicate datastore.
- Secret values never go in this repo, Notion, Jira or chat; use the central secrets service.
- Assistant sessions get no unrestricted host/file authority; destructive personal-file actions need an approved workflow. Domain code uses named capabilities through the approved adapter/MCP contract, not raw SSH.
- Interview capture stays botless: Granola acquires, normalized Meeting Evidence goes to Notion.

## Map

- **Start with `docs/CODEMAP.md`**: a short routing page (every stage, one line per package). Ask one question instead of reading maps: `python -m lifeos.codemap find <text>`; the full map is `docs/codemap.json` (`python -m lifeos.codemap`; a test fails when stale).
- `lifeos/platform/` shared clients (imports nothing above it); `lifeos/jobs|interview|jira|outlook|calendar_bridge/` one OS per package (platform only); `lifeos/sources/` producers (any OS); `lifeos/run.py` stage registry, one `lazy("module", "function")` line per stage; `.github/workflows/hourly.yml` runs stages (`workflow_dispatch` is at the 25-input limit: add none). `tests/` mirrors `lifeos/`; fakes in `tests/kit`; `tests/contracts/` enforce the rules below.
- Docs: `docs/DECISIONS_INDEX.md` (find), `python -m lifeos.decisions show D<n>` (one decision; never read `docs/DECISIONS.md` whole; new numbers only from `python -m lifeos.decisions claim`), `docs/SETUP.md` (secrets, manual steps), `docs/MASTER_ROADMAP.md` (dated WBS and evidence, not a live plan: priorities are in Jira `LOS`, delivery status in `LIFE`); `docs/ROADMAP.md` is legacy.

## Commands

- **Tests: `sh tools/test.sh`** (any host; finds Python 3.12, runs what CI runs: affected tests on a branch, `--all` for everything). One file: `python -m unittest tests.jira.test_jira`.
- **Jira reads and searches:** always pass `fields` (docs/platform/agent-jira-settings.md). **Receipts:** docs/platform/receipt-template.md.
- `git add` new files BEFORE running the suite (the privacy test checks staged files only).
- **GitHub from a session** (where GraphQL is blocked, `gh pr create` and `gh pr ready` fail): open a PR with `gh api repos/jimmarkunas/life-os-v7/pulls --input pr.json`; draft to ready with `gh api -X POST repos/jimmarkunas/life-os-v7/pulls/N/ccr/ready_for_review`; merge with `gh api -X PUT repos/jimmarkunas/life-os-v7/pulls/N/merge -f merge_method=squash`.
- **After a squash merge** the Stop hook warns about "unpushed commits"; they are in main: `git fetch origin main && git checkout -B main origin/main` clears it. A warning on an unmerged branch is real.
- **Jira workflows** cannot be edited through the Atlassian connection: use the Jira admin page in Chrome once; do not retry through the API. Jira search lags after a write: read the issue by key.

## Delivery rules for every epic (Jim, November 11, 2026; source: LOS-119)

Apply these to every new epic or multi-ticket piece of work (written in LOS-119, which links here).

1. **Fewer, bigger tickets.** Target at most 6 build tickets per epic. One ticket = one outcome with one done-when that is measured, not a checklist of sub-steps. No ticket whose only purpose is a comment, a table or a watch.
2. **One approval pass at the start.** Before work begins, list every decision and protected step Jim will be asked for, and get yes or no on all of them at once. Standing guardrails for the epic: agents merge their own green PRs, close their own tickets, and revert on failure. Jim types protected approvals in the Oracle Claude Code chat, as before. Credential deletion and rotation stay outside any blanket yes.
3. **Measurement never waits on the hourly schedule.** Any before/after measurement is a manual workflow dispatch, back to back, with a stated run count and a deadline (default: 3 runs, each started as soon as the last finishes). A scheduled run is only used when the thing being measured is the schedule itself.
4. **Watches live outside the epic.** A time-gated check (for example a read scheduled for a later date) is its own scheduled item with a date. It never keeps an epic open.
5. **An epic closes only when its done-when is measured.** If follow-up work remains, it is named up front as a second epic, not discovered after closing.
6. **Dependency pre-flight per ticket.** Before release, one line: what outside our control can fail this ticket (mirrors, third-party sites, sign-ins, rate limits), and the fallback (cache, retry, skip). Example: skip the apt install on a Chromium cache hit.
7. **Estimate in gates, not effort.** Every epic plan states the critical path: build time, gated waits (runs, approvals, time-gated checks), and external risks, with a wall-clock total. Effort-only estimates are not given.
8. **One end-to-end prompt, one final receipt.** Work is handed to Oracle Claude Code as one prompt with a goal, a deadline and ordered steps. Receipts are per epic, not per step, and always end with context, next steps and exact how-to-verify steps. Ticket numbers always come with plain words on what the ticket is for.
9. **Claude does not relay.** Claude reads tickets, repo and tools itself and never asks Jim to hunt for names, ids, settings or dates; never asks him to close VS Code or Codex; never opens sign-in pages on its own.

## Rules (each enforced by a test)

- Layers: platform imports nothing; OS packages import only platform; sources may import any OS.
- Public repo: never commit, post or log personal email addresses, full personal names, phone numbers, street or postal addresses, employer or salary details, resume or mailbox content, secrets, or Notion page and database IDs (code, tests, commits, PRs, issues, logs, errors); use example.com and invented names; output is counts and fixed codes (full rule: docs/PRIVACY.md). On a leak: stop, open a removal PR, tell Jim.
- No sending or deleting mail or events (tests forbid DELETE, /send, sendMail, /forward, permanentDelete); moves and writes are read back.
- Fail closed: a full result or an error, never partial. Dry run is the default; writes need live.
- Isolated jobs (Interview, Jira, Outlook) blank every workflow-level secret they do not use.
- Keep diffs small. No new framework or dependency without asking. Stop and report on a schema change.
- **Net size.** A ticket that adds code states in its description the lines it expects to add and what it removes. The pull request's Net size section pastes `python -m lifeos.platform.net_size`; if lifeos/ net is above zero it says what the change removes or why nothing can be removed (LIFE-625).

## Reporting

Counts only; say what you could not verify (real data, live runs). **Times for Jim: Central Time only** (labeled CT, 12-hour; never UTC, not even in brackets; convert logs, APIs and cron lines first; take the time from `python -m lifeos.platform.ct_stamp`).

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
