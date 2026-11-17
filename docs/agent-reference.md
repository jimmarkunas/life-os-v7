# LIFE OS V7 — agent reference (the full guide that AGENTS.md summarizes)

Moved here from AGENTS.md by LIFE-766 with no rule changed. Read the section a task needs; [AGENTS.md](AGENTS.md) is the one-page boot guide.

V7 is the active LIFE OS implementation. V1 (`life-os-automation`) and V2 (`life-os-v2`) are read-only references: extract the smallest proven mechanic only. Core rule (D54): V7 must run correctly with ChatGPT completely unavailable. This guide summarizes; the [Development Policy](docs/life-os-development-policy.md) governs (read only what you need).

## Delivery flow v3 — the whole job (Jim, November 9, 2026)

1. **Pick.** Jim named a ticket → that one. Else the top `Agent Go` leaf ticket by Priority, then Rank (any session; `lane-*` is a hint, not a gate). Moving a ticket to Agent Go IS the approval, and an agent may make it unless the ticket names a protected step ([rule](docs/development-policy/04-work-selection-lifecycle-reconciliation.md)). Start with transition ID 14; resuming from blocked, UAT, Done or Cancelled uses "Resume work".
2. **Gate.** `python -m lifeos.sources.devtools.readiness` (`issue-key LIFE-nnn`, or `issue-mcp FILE KEY` on an MCP read saved unmodified) checks exactly three things: the key is valid, the ticket has a Done-when line (any Given/When/Then row or bullet under "Done when"), and every `Blocks` predecessor is Done.
3. **Build.** One branch `life/LIFE-nnn/<slug>`, one PR, the ticket's fast proof first. No claim file; Jim's yes is needed only for: a new or widened workflow `permissions` block or write permission; adding, moving or exposing a credential; deleting a GitHub value or production data; spend beyond the ticket's budget. Ask once, at release or when found; record one `[approvals]` line. Everything else merges on same-head green CI with no label or file list. New stage: its code and its workflow step; `sh tools/preflight.sh` writes the registry rows (tools/regen.py --commit) as one commit. No new stage, step, label, status or check that only manages Jira tickets or process unless Jim asks for it in chat (LIFE-767; `jira-fill` was the last).
4. **Land and close.** Open the PR and enable auto-merge in the same step: `gh pr merge <n> --auto --squash --match-head-commit <sha>`; a PR touching one of the four items above waits: `[waiting on] Jim: <the one thing>`. Merged on same-head green CI with local proof IS Done: close it in one call: `python -m lifeos.jira.closeout KEY... --pr N --sha S --run R --proof KEY=text --live` (CT stamp, one receipt of PR link, what changed, how verified, what is unverified; Done; read-back). A live read-back, deploy check or watch is its own Human UAT item or follow-up ticket, never a hold on the parent. Jim reopens it or files a Bug. Then the next (`NO_ELIGIBLE_WORK` if none).
5. **Optional.** Label `needs-uat`: stop at "Human UAT" with exact steps for Jim; only for new user-facing UI or judgment calls.

**One yes is the go (LIFE-755).** When Jim says yes in chat or a comment (naming the ticket or batch), record it in a comment, move the ticket to Agent Go yourself, drop `planning-only` and `waiting-on-jim`, and start. That one yes covers every step and batch of the ticket: continue at each step's ready time without another message. When a blocker closes, move each already-approved dependent to Agent Go yourself. STOP stays only for actions with no recorded approval: credentials, PII, deleting data or secrets, production changes; every PII rule is unchanged.

**Release rule (LIFE-755).** A ticket whose Done-when says STOP or ask when a prerequisite is missing is not released to Agent Go: it stays To Do with an "is blocked by" link to the ticket that builds the prerequisite, and its scope is not edited.

**Scoping.** One page, at most 2,000 characters: Outcome, 3 to 5 Done-when checks, Not in scope. One execution ticket is one PR (or sheet edit), pasteable. Interview answers and discovery logs go in a linked page or comments, never the description.

**Ticket truth and identity.** A plan change is an edit to the ticket plus one line `Scope change: <what> — Jim, <time CT>`; the description beats comments. The claiming agent fixes the ticket's text when reality changes; a superseded ticket is Cancelled naming its replacement. Add one label (`agent-claude-code`, `agent-hermes`, `agent-chatgpt`) and start Jira comments with `[agent-…]`. One Jira receipt per ticket: moving it to In Progress is the claim; the receipt is the last comment. A mid-ticket comment only when waiting on Jim: `[waiting on] Jim: <the one thing>` and label `waiting-on-jim`. No pause stamps or takeover notes.

## Where truth lives (one owner each)

| Thing | Owner | Not here |
|---|---|---|
| Ticket status, approval, receipt, acceptance | Jira LIFE | chat, Notion, comments-as-approval |
| Code, tests, CI, this guide, the policy | GitHub V7 | Notion copies |
| Product requirements and decisions | Notion Product Requirements database for detail; Jira LOS for scoping and research | Jira descriptions (link, don't copy) |
| Runtime schedule and secrets | Production Contract, central secrets service | this repo, Jira, chat |

Whoever changes a fact updates its owner in the same step and links it; a copy elsewhere becomes a link.

## Fast path

Read the named LIFE issue and only the missing authoritative context; reuse evidence; parallelize independent reads; verify every consequential write; never reopen discovery or duplicate planning artifacts.

- **Bootstrap target 60 s**: run `START_EPOCH=$(date -u +%s)` first; after the essential reads report `bootstrap_seconds` and the remote-read count. Warm resumes recheck only live Jira status/ownership and the `origin/main` SHA. Jira: only summary, status, owner, blockers, acceptance (omitted = UNVERIFIED). GitHub: main SHA, PR head/base, changed files.
- **Authority order:** Jim's current instruction → Production Contract (runtime/scheduling) → Development Policy (engineering) → LOS (product) → Notion requirements → LIFE issue (delivery) → existing code. On conflict, use the owning authority; stop before mutating.
- **Scope:** LOS outcome → LIFE Epic → Tasks → Sub-tasks. A missing Done-when line or an unfinished `Blocks` predecessor returns `QUEUE_NOT_READY`.
- **Execution gate:** only a named Jim instruction or an issue Jim moved to Agent Go permits work; a Jira comment or agent message alone grants nothing. Keep one writer per surface and protected-operation approval.
- **Closure:** merged on same-head green CI plus local proof (tests, fixture replays and dry runs) → Jira update. Never wait for, or start, an hourly, scheduled, on-demand or benchmark run to prove a change; a live look is a non-blocking watch, and a fault it shows is a Bug under LIFE-615. Report `DONE`, `PARTIAL`, `BLOCKED` or `UNVERIFIED` and the next responsible action.
- **No new runtime:** no paid provider, scheduler, orchestrator, sync store, proxy or governance layer without Jim's explicit approval. A Tech Lead handoff can authorize one specified package, not autonomous pull. `docs/MASTER_ROADMAP.md` never selects work. CI runs `python -m lifeos.sources.devtools.readiness repo`.
- **Deadline Mode:** follow CLAUDE.md and the policy (absolute `LIFEOS_DEADLINE_EPOCH`, never reset, delivery claimed only after read-back).

## Buzz pilot entrypoint — only for LIFE-216

[docs/buzz-implementation-roadmap-v1.md](docs/buzz-implementation-roadmap-v1.md) is the frozen 25-step contract and LIFE-216 the evidence ledger; LOS-70 owns product decisions. No Agent Go, credentials, paid usage, live integration or deployment authority comes from this pointer.

## OpenClaw / Oracle edge

Before OpenClaw, Oracle, channel, local-model, assistant, meeting-capture or AI-ops work, read `docs/EDGE_GATEWAY_SCOPE.md` and the relevant `docs/MASTER_ROADMAP.md` row.
- OpenClaw is a bounded capability/control plane; V7 and domain owners keep business rules, persistence and scheduling. No second scheduler or duplicate datastore.
- Secret values never go in this repo, Notion, Jira or chat; use the central secrets service.
- Assistant sessions get no unrestricted host/file authority; destructive personal-file actions need an approved workflow. Domain code uses named capabilities through the approved adapter/MCP contract, not raw SSH.
- Interview capture stays botless: Granola acquires, normalized Meeting Evidence goes to Notion.

## Map

- **Start with `python -m lifeos.sources.devtools.codemap`** (about 1 s; writes untracked `docs/CODEMAP.md`: every stage, one line per package), then read it. Ask one question instead of reading maps: `python -m lifeos.sources.devtools.codemap find <text>`; the full map is `docs/codemap.json`, also generated and not committed (LIFE-644).
- `lifeos/platform/` shared clients (imports nothing above it); `lifeos/jobs|interview|jira|outlook|calendar_bridge/` one OS per package (platform only); `lifeos/sources/` producers (any OS); `lifeos/run.py` stage registry, one `lazy("module", "function")` line per stage; `.github/workflows/hourly.yml` runs stages (`workflow_dispatch` is at the 25-input limit: add none). `tests/` mirrors `lifeos/`; fakes in `tests/kit`; `tests/contracts/` enforce the rules below.
- Docs: `docs/DECISIONS_INDEX.md` (find), `python -m lifeos.sources.devtools.decisions show D<n>` (one decision; never read `docs/DECISIONS.md` whole; new numbers only from `python -m lifeos.sources.devtools.decisions claim`), `docs/SETUP.md` (secrets, manual steps), `docs/MASTER_ROADMAP.md` (dated evidence, not a live plan: priorities in Jira `LOS`, status in `LIFE`).

## Commands

- **Tests:** `sh tools/preflight.sh` before every push (the quick gate: every contract test, the repo-wide scanners and the tests that name or mirror your changed files, in parallel; it never runs the whole suite because CI does, so fix once if red and do not rerun the full suite by hand; `sh tools/automerge.sh <pr>` exits 4 without its PASS for HEAD); `sh tools/preflight.sh --all` or `sh tools/test.sh --all` only when you want everything (parallel shards, one per core); one file `python -m unittest <module>`; setup `sh tools/setup-dev.sh`. Tickets on the same files may share one PR and receipt (LIFE-762).
- **Jira connector:** `mcp__claude_ai_Atlassian_MCP__*` only; Rovo connectors are retired.
- **Jira reads and searches:** always pass `fields` (docs/platform/agent-jira-settings.md). **Receipts:** docs/platform/receipt-template.md.
- **New tickets (LIFE-741):** Create every ticket under an epic (bugs under LIFE-615); Component and Fix Version are filled for you, set them only to override.
- `git add` new files BEFORE running the suite (the privacy test checks staged files only).
- **GitHub from a session** (GraphQL blocked: `gh pr create`/`gh pr ready` fail): (R=repos/jimmarkunas/life-os-v7) open a PR with `gh api $R/pulls --input pr.json`; draft to ready with `gh api -X POST $R/pulls/N/ccr/ready_for_review`; merge with `gh api -X PUT $R/pulls/N/merge -f merge_method=squash`.
- **Updating your own pushed branch (LIFE-663):** after a rebase or amend of your `life/...` branch run `sh tools/push_life_branch.sh` (lease on the fetched tip, current `life/*` branch only); else plain `git push`. Force, delete, `+refspec` pushes and `git branch -d/-D` stay denied.
- **After a squash merge** the Stop hook's "unpushed commits" warning is stale (they are in main): `git fetch origin main && git checkout -B main origin/main`. Real on an unmerged branch.
- **Jira workflows** cannot be edited through the Atlassian connection: use the Jira admin page once; do not retry via the API. Jira search lags after a write: read the issue by key.

## Delivery rules for every epic (Jim, November 11, 2026; source: LOS-119)

1. **Fewer, bigger tickets.** Target at most 6 build tickets per epic. One ticket = one outcome with one done-when that is measured, not a checklist of sub-steps. No ticket whose only purpose is a comment, a table or a watch.
2. **One approval pass at the start.** Before work begins, list every decision and protected step Jim will be asked for, and get yes or no on all of them at once. Standing guardrails: agents merge their own green PRs, close their own tickets, revert on failure; Jim types protected approvals in the Oracle Claude Code chat. Credential deletion and rotation stay outside any blanket yes.
3. **Measurement never waits on the hourly schedule.** Any before/after measurement is a manual workflow dispatch with a stated run count and deadline (default: 3 back-to-back runs). A scheduled run is only used when the thing being measured is the schedule itself.
4. **Watches live outside the epic.** A time-gated check is its own scheduled item with a date. It never keeps an epic open.
5. **An epic closes only when its done-when is measured.** If follow-up work remains, it is named up front as a second epic, not discovered after closing.
6. **Dependency pre-flight per ticket.** Before release, one line: what outside our control can fail this ticket (mirrors, third-party sites, sign-ins, rate limits), and the fallback (cache, retry, skip).
7. **Estimate in gates, not effort.** Every epic plan states the critical path: build time, gated waits (runs, approvals, time-gated checks), and external risks, with a wall-clock total.
8. **One end-to-end prompt, one final receipt.** Work is handed to Oracle Claude Code as one prompt with a goal, a deadline and ordered steps. Receipts are per epic and end with context, next steps and exact how-to-verify steps. Ticket numbers always come with plain words on what the ticket is for.
9. **Claude does not relay.** Claude reads tickets, repo and tools itself, never asks Jim to hunt for names, ids, settings or dates or to close VS Code or Codex, and never opens sign-in pages on its own.

## Rules (each enforced by a test)

- Layers: platform imports nothing; OS packages import only platform; sources may import any OS.
- Public repo: never commit, post or log personal email addresses, full personal names, phone numbers, street or postal addresses, employer or salary details, resume or mailbox content, secrets, or Notion page and database IDs (code, tests, commits, PRs, issues, logs, errors); use example.com and invented names; output is counts and fixed codes (full rule: docs/PRIVACY.md). On a leak: stop, open a removal PR, tell Jim.
- No sending or deleting mail or events (tests forbid DELETE, /send, sendMail, /forward, permanentDelete); moves and writes are read back.
- Fail closed: a full result or an error, never partial. Dry run is the default; writes need live.
- Isolated jobs (Interview, Jira, Outlook) blank every workflow-level secret they do not use.
- Keep diffs small. No new framework or dependency without asking. Stop and report on a schema change.
- **Net size.** A ticket that adds code states the lines it expects to add and remove. The PR's Net size section pastes `python -m lifeos.platform.net_size`; with lifeos/ net above zero it says what is removed or why nothing can be (LIFE-625).

## Reporting

Counts only; say what you could not verify (real data, live runs). **Times for Jim: Central Time only** (labeled CT, 12-hour; never UTC, not even in brackets; convert log, API and cron times; take it from `python -m lifeos.platform.ct_stamp`).
