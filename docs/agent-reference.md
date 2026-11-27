# LIFE OS V7: agent reference (code map, commands, epic delivery rules)

Detail that [AGENTS.md](../AGENTS.md) points to. The rules are in the [Development Policy](life-os-development-policy.md); this file adds no rule of its own beyond the test-enforced list below. Read only the section a task needs.

V7 is the active LIFE OS implementation. V1 (`life-os-automation`) and V2 (`life-os-v2`) are read-only references. V7 must run correctly with ChatGPT unavailable (D54).

## Readiness check

`python -m lifeos.sources.devtools.readiness issue-key LIFE-nnn` (or `issue-mcp FILE KEY` on an unmodified MCP read) refuses a ticket with an invalid key, no Done-when line, an unfinished `Blocks` predecessor, or an unchecked **Jim must do first** item. CI runs `python -m lifeos.sources.devtools.readiness repo` on the source index.

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

- **Rate limits:** Never poll or loop on rate-limited APIs (GitHub, Notion REST): use git for repo state, batch single calls.
- **Format:** run `ruff format lifeos tools` before you commit; CI runs `ruff format --check` on both.
- **Tests:** `sh tools/preflight.sh` before every push (quick gate: contract tests, scanners and tests mirroring your changed files; never the whole suite (CI does), so fix once if red; `sh tools/automerge.sh <pr>` exits 4 without its PASS for HEAD); `sh tools/preflight.sh --all` or `sh tools/test.sh --all` for everything; one file `python -m unittest <module>`; setup `sh tools/setup-dev.sh`. Tickets on the same files may share one PR (LIFE-762).
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
2. **One approval pass at the start.** Before release, every decision and protected step Jim will be asked for is in the ticket's **Jim must do first** section, and Jim clears it once. Standing guardrails: agents merge their own green PRs, close their own tickets, revert on failure. Credential deletion and rotation stay outside any blanket yes.
3. **Measurement never waits on the hourly schedule.** Any before/after measurement is a manual workflow dispatch with a stated run count and deadline (default: 3 back-to-back runs). A scheduled run is only used when the thing being measured is the schedule itself.
4. **Watches live outside the epic.** A time-gated check is its own scheduled item with a date. It never keeps an epic open.
5. **An epic closes only when its done-when is measured.** If follow-up work remains, it is named up front as a second epic, not discovered after closing.
6. **Dependency pre-flight per ticket.** Before release, one line: what outside our control can fail this ticket (mirrors, third-party sites, sign-ins, rate limits), and the fallback (cache, retry, skip).
7. **Estimate in gates, not effort.** Every epic plan states the critical path: build time, gated waits (runs, approvals, time-gated checks), and external risks, with a wall-clock total.
8. **One end-to-end prompt, one final receipt.** Work is handed to Claude Code as one prompt with a goal and ordered steps. Receipts are per epic and end with context, next steps and exact how-to-verify steps. Ticket numbers always come with plain words on what the ticket is for.
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
