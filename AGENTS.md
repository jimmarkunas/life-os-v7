# LIFE OS V7 — agent guide (read this, not the whole repo)

V7 is the active LIFE OS implementation. V1 (`life-os-automation`) and V2 (`life-os-v2`) are read-only references: extract the smallest proven mechanic only. Core rule (D54): V7 must run correctly with ChatGPT completely unavailable. This guide is a summary; the [Development Policy](docs/life-os-development-policy.md) governs (read only the sections you need, especially work selection and the Jira-first lifecycle).

## Delivery flow v3 — the whole job (Jim, November 9, 2026)

1. **Pick.** Jim named a ticket → that one. Else the top `Agent Go` leaf ticket in your lane (`lane-*` label) by Priority, then Rank. Moving a ticket to Agent Go IS the approval. Start with transition ID 14; resuming from blocked, UAT, Done or Cancelled uses "Resume work".
2. **Gate.** `python -m lifeos.readiness` (`issue-key LIFE-nnn`, or `issue-mcp FILE KEY` on an MCP read saved unmodified) checks exactly three things: the key is valid, the ticket has at least one Done-when line (any Given/When/Then row or any bullet under "Done when"), and every `Blocks` predecessor is Done. Anything else prints a `warn` line and exits 0; exit 2 only on those three.
3. **Build.** One branch `life/LIFE-nnn/<slug>`, one PR, the ticket's fast proof first. No claim file, no preflight; Concord `start_work`/`finish_work` are optional, never a refusal. Protected steps (secrets, production, spend, user data, host changes) wait for their owned human approval.
4. **Land and close.** Merge when same-head CI is green and no protected item is touched. Post one comment on the ticket: PR link, what changed, how it was verified, what is unverified. Move the ticket to Done yourself; Jim reopens it or files a Bug. Then pick the next ticket and repeat until Agent Go is empty (`NO_ELIGIBLE_WORK`).
5. **Optional.** Label `needs-uat` means stop at "Human UAT" with exact steps for Jim; use it only for new user-facing UI or anything judgment-based.

**Scoping.** A scoping note is one page, at most 2,000 characters: Outcome, 3 to 5 Done-when checks, Not in scope. The agent cuts execution tickets from it in the same step. One execution ticket is one PR (or one sheet edit), written so it can be pasted into Claude Code with no questions: exact targets, steps, done-when. Interview answers and discovery logs go in a separate linked page or in comments, never in the ticket description (a LIFE description over 4,000 characters draws a `warn`).

**Ticket truth and identity.** A plan change is an edit to the ticket plus one line `Scope change: <what> — Jim, <time CT>`; the description beats comments. The claiming agent fixes the ticket's text the same session reality changes; a superseded ticket is Cancelled with a line naming its replacement. Add one label (`agent-claude-code`, `agent-hermes`, `agent-chatgpt`) and start Jira comments with `[agent-…]`; labels aid reading, not enforcement.

## Where truth lives (one owner each)

| Thing | Owner | Not here |
|---|---|---|
| Ticket status, approval, receipt, acceptance | Jira LIFE | chat, Notion, comments-as-approval |
| Code, tests, CI, this guide, the policy | GitHub V7 | Notion copies |
| Product requirements and decisions | Notion Product Requirements database for detail; Jira LOS for scoping and research | Jira descriptions (link, don't copy) |
| Runtime schedule and secrets | Production Contract, central secrets service | this repo, Jira, chat |

Whoever changes a fact updates its owner in the same step and links it; a copy elsewhere is replaced by a link, not edited.

## Fast path

Read the named LIFE issue first and only the missing authoritative context; reuse unchanged evidence; run independent reads in parallel; verify every consequential write; never reopen discovery or create duplicate planning artifacts.

- **Bootstrap target 60 s** (not permission to skip checks): run `START_EPOCH=$(date -u +%s)` first; after the essential reads report `bootstrap_seconds` and the remote-read count. Warm resumes recheck only live Jira status/ownership and the `origin/main` SHA. Jira: exact key and only summary, status, owner, blockers, acceptance (add `duedate`/`parent` when they matter; an omitted field is UNVERIFIED). GitHub: main SHA, PR head/base, changed files, then touched files only.
- **Authority order:** Jim's current instruction → Production Contract (runtime/scheduling) → Development Policy (engineering) → LOS (product) → Notion requirements → LIFE issue (delivery) → existing code. On conflict, use the owning authority and stop before mutating.
- **Scope:** LOS outcome → LIFE Epic → Tasks → executable Sub-tasks. A missing Done-when line or an unfinished `Blocks` predecessor returns `QUEUE_NOT_READY`; other gaps are warnings.
- **Execution gate:** only a named Jim instruction or an issue Jim moved to Agent Go permits work; a Jira comment or agent message alone grants nothing. Keep one writer per surface and protected-operation approval.
- **Closure:** targeted proof → real outcome → read-back → Jira update. Report `DONE`, `PARTIAL`, `BLOCKED` or `UNVERIFIED` and the next responsible action.
- **No new runtime:** no paid provider, scheduler, orchestrator, sync store, proxy or governance layer without Jim's explicit approval. Do not wait for ChatGPT to pick tickets; a Tech Lead handoff can authorize one specified package, not autonomous pull. `docs/MASTER_ROADMAP.md` never selects work. `python -m lifeos.readiness repo` checks the index, links, owners and size limits (CI runs it).
- **Deadline Mode** (when Jim states a deadline): first run `TZ=America/Chicago date '+%Y-%m-%d %H:%M:%S %Z'; date -u +%s`, derive and keep an absolute `LIFEOS_DEADLINE_EPOCH`, recheck remaining seconds at every milestone, apply the policy's 50/75/90% cuts, never reset it on handoff, and claim delivery only after real read-back.

## Buzz pilot entrypoint — only for LIFE-216

Use [docs/buzz-implementation-roadmap-v1.md](docs/buzz-implementation-roadmap-v1.md) as the frozen 25-step contract and LIFE-216 as the execution/approval/evidence ledger; LOS-70 owns product decisions. No Agent Go, credentials, paid usage, live integration or deployment authority arises from this pointer. No new scheduler/orchestrator.

## OpenClaw / Oracle edge

Before OpenClaw, Oracle, channel, local-model, assistant, meeting-capture or AI-ops work, read `docs/EDGE_GATEWAY_SCOPE.md` and the relevant `docs/MASTER_ROADMAP.md` row (capability registry, approval gates, invocation).
- OpenClaw is a bounded capability/control plane; V7 and domain owners keep business rules, persistence and scheduling. No second scheduler or duplicate datastore.
- Secret values never go in this repo, Notion, Jira or chat; use the central secrets service and runtime references.
- Assistant sessions get no unrestricted host/file authority; destructive personal-file actions need an approved workflow. Domain code uses named capabilities through the approved adapter/MCP contract, not raw SSH.
- Interview capture stays botless: Granola is the acquisition layer, normalized Meeting Evidence goes to Notion.

## Map

- **Start with `docs/CODEMAP.md` (or `docs/codemap.json`)**: generated index of stages, modules, workflows, secret names, tables and decisions (`python -m lifeos.codemap`; a test fails when it is stale). Find the file there, read only that file.
- `lifeos/platform/` shared clients (imports nothing above it); `lifeos/jobs|interview|jira|outlook|calendar_bridge/` one OS per package (platform only); `lifeos/sources/` producers (any OS); `lifeos/run.py` stage registry, one `lazy("module", "function")` line per stage; `.github/workflows/hourly.yml` runs stages (`workflow_dispatch` is at the 25-input limit: add none). `tests/` mirrors `lifeos/`; fakes live in `tests/kit`; `tests/contracts/` enforce the rules below.
- Docs: `docs/DECISIONS_INDEX.md` (find), `docs/DECISIONS.md` (grep, never read whole; new numbers only from `python -m lifeos.decisions claim`), `docs/SETUP.md` (secrets, manual steps), `docs/MASTER_ROADMAP.md` (dated technical WBS and evidence, not a live plan: priorities are in Jira `LOS`, delivery status in Jira `LIFE`). `docs/ROADMAP.md` is legacy.

## Commands

- All tests: `python -m unittest discover -s tests -t .` (about 2 s); good only if it prints a line starting `OK`. One file: `python -m unittest tests.jira.test_jira`.
- **On the Mac** (LIFE-553; the Mac's own `python3` is 3.9 and has no PyYAML, and `requirements.lock` carries Linux hashes, so a bare run fails 35 to 260 tests that CI passes). From the repo root, this one line builds a throwaway Python 3.12 environment once with the locked versions, puts it first on PATH, and runs the suite; expect 4276 tests, `OK`, about 2.5 minutes: `[ -d ~/.venvs/lifeos-tests ] || { python3.12 -m venv ~/.venvs/lifeos-tests && grep -hE '^[A-Za-z0-9_.-]+==' requirements.lock requirements-test.lock | sed 's/ *\\$//' | ~/.venvs/lifeos-tests/bin/pip install -q -r /dev/stdin; }; PATH=~/.venvs/lifeos-tests/bin:$PATH python -m unittest discover -s tests -t .`
- `git add` new files BEFORE running the suite: the privacy test checks committed/staged files only.
- **GitHub from a session** (GraphQL is blocked; `gh pr create` and `gh pr ready` fail): open a PR with `gh api repos/jimmarkunas/life-os-v7/pulls --input pr.json` (title, head, base, body, draft); draft to ready with `gh api -X POST repos/jimmarkunas/life-os-v7/pulls/N/ccr/ready_for_review`; merge with `gh api -X PUT repos/jimmarkunas/life-os-v7/pulls/N/merge -f merge_method=squash`.
- **After a squash merge** the branch is deleted and the Stop hook (a platform script, not ours) warns about "unpushed commits". The commits are in main: run `git fetch origin main && git checkout -B main origin/main` and the warning clears. A warning on a branch that is not merged is real.
- **Jira workflows** cannot be edited through the Atlassian connection: use the Jira admin page in Chrome, once, and do not retry through the API. Jira search lags after a write: read the issue by key instead.

## Rules (each enforced by a test)

- Layers: platform imports nothing; OS packages import only platform; sources may import any OS.
- Public repo: never commit, post or log personal email addresses, full personal names, phone numbers, street or postal addresses, employer or salary details, resume or mailbox content, secrets, or Notion page and database IDs (code, tests, commits, PRs, issues, logs, errors); use example.com and invented names; output is counts and fixed codes. Full rule: docs/PRIVACY.md. On a leak: stop, open a PR removing it, tell Jim.
- No sending or deleting mail or events (tests forbid DELETE, /send, sendMail, /forward, permanentDelete); moves and writes are read back.
- Fail closed: a full result or an error, never partial. Dry run is the default; writes need live.
- Isolated jobs (Interview, Jira, Outlook) blank every workflow-level secret they do not use.
- Keep diffs small. No new framework or dependency without asking. Stop and report on a schema change.

## Reporting

Counts only; say what you could not verify (real data, live runs). One PR per task. **Times for Jim: Central Time only** (US Central, America/Chicago, labeled CT, 12-hour, e.g. 4:34 PM CT; never UTC, not even in brackets; convert logs, APIs and cron lines first; run `TZ=America/Chicago date` before stating a time).

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

Replace <agent-id> with the id the SessionStart hook printed for this session.
A monitor may not inherit the session's environment, so it cannot work the id out itself.

In Grok Build, keep this session reachable while idle by starting one persistent
monitor for `concord inbox watch --agent <agent-id> --provider grok`. Do not start a duplicate when that monitor is active.

In Cursor, start exactly one Cursor background Shell task for `concord inbox watch --agent <agent-id> --provider cursor --once`.
Leave it active when the turn ends. When Cursor resumes you with its completion,
answer the emitted peer message and immediately start a fresh background monitor.

In Gemini CLI, start the exact monitor command supplied by the SessionStart hook
(`concord inbox watch --agent <agent-id> --provider gemini --once`) with `run_shell_command` and `is_background: true`. Project
settings inject background completion into the agent. Answer the peer message,
then immediately start a fresh background monitor.

In Antigravity, start exactly one background command for `concord inbox watch --agent <agent-id> --provider antigravity --once`
and end the turn without waiting on it. Its completion wakes the idle session:
answer the peer message, then immediately start a fresh background monitor.
<!-- concord:end -->
