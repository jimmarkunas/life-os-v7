# LIFE OS V7 — agent guide (read this, not the whole repo)

V7 is the active LIFE OS implementation. V1 (`life-os-automation`) and V2 (`life-os-v2`) are read-only references: extract the smallest proven mechanic only. Core rule (D54): V7 must run correctly with ChatGPT completely unavailable. This guide is a summary; the [Development Policy](docs/life-os-development-policy.md) governs (read only the sections you need, especially work selection and the Jira-first lifecycle).

## Agent loop — the whole job

1. **Pick.** Jim named an issue → that one. Else resume your own In Progress issue. Else take the top `Agent Go` leaf issue in your lane (`lane-*` label) by Priority, then Rank. Never pick an Epic or parent, an issue with an unfinished `Blocks` predecessor, or anything outside Agent Go. Start with transition ID 14; resuming from blocked, UAT, Done or Cancelled uses "Resume work".
2. **Gate.** `python -m lifeos.readiness issue ISSUE.json` must print `ready`; on exit 2 quote its `refused` line and stop. Add no refusal reasons of your own.
3. **Claim.** `python -m lifeos.readiness preflight --pre CLAIM.json STATE.json`, record the claim, then `python -m lifeos.readiness preflight CLAIM.json STATE.json` before every mutation (the validator fetches `origin/main` itself; your `base_sha` must equal it). One writer per surface.
4. **Build.** One branch `life/LIFE-nnn/<slug>`, one PR, the issue's fast proof first. Protected steps (secrets, production, spend, user data, host changes) wait for their owned human Sub-task and named approval.
5. **Land.** Open the PR; merge when same-head CI is green and nothing outside Part 07 items 3/3a is touched. Post one receipt on the issue (OUTCOME, REMOVAL, PROOF, REMOTE SHA, NEXT) and move it to Agent UAT. Human UAT only where judgment is needed, with exact steps for Jim. Never mark Done from CI or a merge.
6. **Next.** After verified Done repeat from 1; nothing eligible → `NO_ELIGIBLE_WORK`. Jira or queue data missing → `QUEUE_NOT_READY — <exact gap>`.

## Delivery flow v2 (Jim, November 5, 2026)

- **One outcome ticket** per feature: outcome, Given/When/Then, `needs` and `decisions` rows ({what, proof, status}), protected steps, not-in-scope. Readiness refuses any open row.
- **One approval:** Jim's release (ID 5) or named instruction covers everything inside the written scope. A plan change is an edit to the ticket plus one line `Scope change: <what> — Jim, <time CT>`, never a pile of approval comments. The description beats comments.
- **One receipt, one acceptance:** see step 5. **Ticket truth:** the claiming agent fixes the ticket's text the same session reality changes; a superseded ticket is Cancelled with a line naming its replacement.
- **Agent identity:** add one label (`agent-claude-code`, `agent-hermes`, `agent-chatgpt`) and start Jira comments with `[agent-…]`. Labels aid reading, not enforcement.

## Where truth lives (one owner each)

| Thing | Owner | Not here |
|---|---|---|
| Ticket status, approval, receipt, acceptance | Jira LIFE | chat, Notion, comments-as-approval |
| Code, tests, CI, this guide, the policy | GitHub V7 | Notion copies |
| Product requirements and decisions | Notion + Jira LOS | Jira descriptions (link, don't copy) |
| Runtime schedule and secrets | Production Contract, central secrets service | this repo, Jira, chat |

Whoever changes a fact updates its owner in the same step and links it; a copy elsewhere is replaced by a link, not edited.

## Fast path

Read the named LIFE issue first and only the missing authoritative context; reuse unchanged evidence; run independent reads in parallel; verify every consequential write; never reopen discovery or create duplicate planning artifacts.

- **Bootstrap target 60 s** (not permission to skip checks): run `START_EPOCH=$(date -u +%s)` first; after the essential reads report `bootstrap_seconds` and the remote-read count. Warm resumes recheck only live Jira status/ownership and the `origin/main` SHA. Jira: exact key and only summary, status, owner, blockers, acceptance (add `duedate`/`parent` when they matter; an omitted field is UNVERIFIED). GitHub: main SHA, PR head/base, changed files, then touched files only.
- **Authority order:** Jim's current instruction → Production Contract (runtime/scheduling) → Development Policy (engineering) → LOS (product) → Notion requirements → LIFE issue (delivery) → existing code. On conflict, use the owning authority and stop before mutating.
- **Scope:** LOS outcome → LIFE Epic → Tasks → executable Sub-tasks. A missing origin, owner/lane, Component/Fix Version, dependency, human prerequisite, Given/When/Then, host, fast proof or stop condition returns `DISCOVERY_NOT_READY` or `QUEUE_NOT_READY`.
- **Execution gate:** only a named Jim instruction or a correctly released Agent Go issue permits work; a Jira comment, status or agent message alone grants nothing. Enforce the one-writer claim, permissions and protected-operation approval.
- **Closure:** targeted proof → real outcome → independent read-back → Jira update. Report `DONE`, `PARTIAL`, `BLOCKED` or `UNVERIFIED` and the next responsible action.
- **No new runtime:** no paid provider, scheduler, orchestrator, sync store, proxy or governance layer without Jim's explicit approval. Do not wait for ChatGPT to pick tickets; a Tech Lead handoff can authorize one specified package, not autonomous pull. `docs/MASTER_ROADMAP.md` never selects work. The ChatGPT connector preflight stays procedural, not code-enforced (LOS-74 Q30). `python -m lifeos.readiness repo` checks the index, links, owners and size limits (CI runs it).
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
- `git add` new files BEFORE running the suite: the privacy test checks committed/staged files only.
- **GitHub from a session** (GraphQL is blocked; `gh pr create` and `gh pr ready` fail): open a PR with `gh api repos/jimmarkunas/life-os-v7/pulls --input pr.json` (title, head, base, body, draft); draft to ready with `gh api -X POST repos/jimmarkunas/life-os-v7/pulls/N/ccr/ready_for_review`; merge with `gh api -X PUT repos/jimmarkunas/life-os-v7/pulls/N/merge -f merge_method=squash`.
- **After a squash merge** the branch is deleted and the Stop hook (a platform script, not ours) warns about "unpushed commits". The commits are in main: run `git fetch origin main && git checkout -B main origin/main` and the warning clears. A warning on a branch that is not merged is real.
- **Jira workflows** cannot be edited through the Atlassian connection: use the Jira admin page in Chrome, once, and do not retry through the API. Jira search lags after a write: read the issue by key instead.

## Rules (each enforced by a test)

- Layers: platform imports nothing; OS packages import only platform; sources may import any OS.
- Public repo: no secrets, names, addresses or message content in code, tests, logs or errors; use example.com and invented names; output is counts and fixed codes.
- No sending or deleting mail or events (tests forbid DELETE, /send, sendMail, /forward, permanentDelete); moves and writes are read back.
- Fail closed: a full result or an error, never partial. Dry run is the default; writes need live.
- Isolated jobs (Interview, Jira, Outlook) blank every workflow-level secret they do not use.
- Keep diffs small. No new framework or dependency without asking. Stop and report on a schema change.

## Reporting

Counts only; say what you could not verify (real data, live runs). One PR per task. **Times for Jim: Central Time only** (US Central, America/Chicago, labeled CT, 12-hour, e.g. 4:34 PM CT; never UTC, not even in brackets; convert logs, APIs and cron lines first; run `TZ=America/Chicago date` before stating a time).
