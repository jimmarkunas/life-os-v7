# LIFE OS Development Policy

**Owner:** Jim Markunas. This one file is the whole policy. Change it by PR with Jim's yes. Edit a rule in place: replace it, never append a dated amendment. Old text goes to [docs/archive/](archive/development-policy-2026-11-20.md), which has no authority and is not read by default. The file stays under 15 KB so an agent reads it once.

## 1. Authority

1. Jim's current instruction.
2. The Production Contract, for schedules and runtime.
3. This policy, for how development works.
4. Notion LIFE OS Platform canon, for architecture.
5. Jira `LIFE` for product priority, delivery status, blockers and order. The `LOS` project is retired (Jim, November 21, 2026): history only. The Jira plan cannot archive it, so it stays in place, read-only by practice.
6. The owning Notion product requirement.
7. The code, for what exists.

Old chats, roadmap prose (`docs/MASTER_ROADMAP.md`) and the archive are background only. `AGENTS.md` and `CLAUDE.md` route to this file and never override it. On a conflict, use the owning authority and stop before changing anything.

| Thing | Owner |
|---|---|
| Ticket status, approval, acceptance | Jira LIFE |
| Code, tests, CI, this policy | GitHub V7 |
| Product requirements and decisions | Notion spec page linked from the owning LIFE Epic |
| Runtime schedule and secrets | Production Contract; Bitwarden |

Link to the owner; never copy a fact into a second place.

## 2. Mission

`user outcome → bounded change → targeted proof → land`. The process stays smaller than the product. If work turns exploratory, historical or process-heavy, stop and simplify.

## 3. Delivery

- **Jim starts every agent run.** No scheduler, timer, poller, `/loop`, watcher or dispatcher may start or wake an agent. A Jira status change starts nothing.
- **Once started, an agent runs end to end:** finish the ticket, then pull the next, until the queue is empty (`NO_ELIGIBLE_WORK`) or a ticket truly needs Jim.
- **Pick:** the ticket Jim named, else the top leaf of `project = LIFE AND status = "Agent Go" AND issuetype != Epic ORDER BY priority DESC, Rank ASC`. Skip a ticket already In Progress (another session has it) or with an unfinished `Blocks` predecessor. Lane labels are hints. Agents never reorder or reprioritize. Several sessions (Mac and Oracle Claude Code) may pull in parallel; one writer per branch and per mutation surface.
- **Claim:** move the ticket to In Progress (transition ID 14). The status is the claim; no comment.
- **Build:** one branch `life/LIFE-nnn/<slug>`, one PR titled `[LIFE-nnn] <outcome>`. Tickets that touch the same files may share one PR. Make the smallest change that delivers the outcome and remove the path it replaces.
- **Test:** `sh tools/preflight.sh` before every push. CI is the full gate: fix once if red.
- **Land:** auto-merge on same-head green CI. No reviewer is required unless GitHub branch protection or the ticket says so; never invent an approval.
- **Agent UAT:** after merge, the agent checks the delivered outcome where it lives (the natural run's receipt, the page, the read-back) against every Done when line. A merged PR on green is not Done by itself.
- **Close:** `python -m lifeos.jira.closeout KEY... --pr N --sha S --run R --proof KEY=text --live` posts the one record (PR, SHA, CI run, outcome proof per Done when line) and moves the ticket to Done. Jim reopens it or files a Bug.
- **Close a `research` ticket:** it has no PR, so skip closeout. Post one findings comment that answers every Done when line (UNCLEAR or UNVERIFIED with the reason is an answer), then move it to Done with transition 15. A `verdict` ticket closes the same way after its authorized writes are read back.
- **Human UAT:** with label `needs-uat`, or when Who verifies names Jim on a live or protected outcome. Stop at Human UAT (transition 19) with exact click-through steps for Jim; Jim's check moves it to Done.
- **Schedules:** adding, moving or changing the cadence of any scheduled workload is a Production Contract change: it is in the ticket's Jim must do first, per workload, before release.
- **Scope:** never change or drop a ticket's stated scope, or swap a stated fix for a workaround, without asking Jim. Optional improvements become a proposal, not extra work.

## 4. Jim must do first (the release rule)

Every LIFE ticket includes **Jim must do first** checkbox items for owner-only: secrets/Bitwarden, permissions/sign-in, spend/paid models, Mac/Oracle host steps, product decisions, Notion UI wireframe approval, and UAT setup. `None` means no prerequisites.

- **Chat approval:** Jim's explicit ChatGPT instruction naming a LIFE ticket and approved scope is sufficient human authorization. ChatGPT records it in the Jira description, checks only proven owner items, verifies live readiness/dependencies/verdict, and moves an eligible ticket to `Agent Go`. No Jira UI click or repeat approval. Ambiguous consent does not count; chat approval never waives safety/feasibility gates or starts an agent.
- **Release:** Missing section or unchecked item blocks `Agent Go`. `lifeos.jira.promote`, auto-release, and `python -m lifeos.sources.devtools.readiness` enforce this.
- **Agent Go** authorizes the written scope; agents do not re-ask.
- **Mid-run need:** Add unmet checkbox, `needs-jim`, one comment; continue independent work.

## 5. What always stops for Jim

These four need Jim's yes. Put them in **Jim must do first** whenever they are known. `tools/workflow_gate.py`, run by preflight, stops on them:

1. Adding or widening a workflow `permissions` block or any write permission.
2. Adding, moving or exposing a credential. Secrets live in a secrets manager (Bitwarden today; Doppler only if Jim picks it), never in the repo, Jira or chat. GitHub's secret store may hold only one bootstrap token per secrets manager, the one that unlocks it (today `LIFEOS_BWS_RUNTIME_TOKEN`); adding one is still Jim's decision (Jim, November 21, 2026).
3. Deleting a GitHub value or production data.
4. Spend or a paid model beyond the ticket's budget.

Everything else is pre-approved, including workflow edits and test or registry edits that follow from an approved change, as long as no check is loosened.

Also never, without Jim:
- send or delete mail or calendar events;
- run a metered Notion query (needs a yes per query). Reads go in this order: an exact page or data source fetch, a saved view, the repository's own Notion API path (`lifeos` readers through the V7 integration), and only then MCP rows or SQL queries, which count as metered;
- open a sign-in or account-picker page;
- delete or rotate a credential (never covered by a blanket yes).

## 6. Privacy (public repository)

Never put any of these in code, tests, commits, PRs, issues, logs or errors:
- personal email addresses, full personal names, phone numbers, or street or postal addresses;
- employer or salary details;
- resume or mailbox content;
- secrets;
- Notion page or database IDs.

Use example.com and invented names. Output is counts and fixed codes. The full rule is [docs/PRIVACY.md](PRIVACY.md); the privacy test enforces it. Private embedded UI never goes to public URLs, public JSON, logs or artifacts.

## 7. Writing tickets

The field list is [`docs/jira/issue-schema.v1.json`](jira/issue-schema.v1.json). A ticket can be pasted into Claude Code and built with no questions:

- **Statement:** a Story as `As a [user], I want [capability], so that [benefit].`, or a Task or Bug as one bounded outcome.
- **Before-state and exact targets:** files, pages, fields, numbers.
- **Done when:** one `Given … when … then …` line, or one bullet, per behavior. A tick without cited proof is not Done.
- **Jim must do first:** see §4.
- **Not in scope.**
- **Who verifies,** by reading or clicking, never by running commands.

**Reuse, never redo:** a ticket that replaces cancelled work reads the old tickets' comments, PRs and data first. Its Before section cites what already exists (merged PRs, live data, stored identifiers, Done evidence) and its steps resume from there.

Keep the description under 4,000 characters. One execution ticket is one PR. Reference secrets by identifier only. Link the Notion requirement instead of copying it. The description beats comments: a scope change is an edit to the description, never a comment alone.

**UI first:** for new or changed UI, show the smallest working preview in the real surface (with synthetic data where live data is not authorized), and get Jim's acceptance before expanding the backend. For changed results in an existing UI, show current versus intended output. A UI-free change names its downstream outcome and proof.

**Hierarchy:** LIFE Epic → Task → Sub-task.
- Every ticket sits under an epic; Bugs go under LIFE-615.
- Every ticket has exactly one Component (its owning OS) and the matching Fix Version `<component> X.Y.Z`. These are filled automatically (LIFE-741). Agents never invent a release.
- Done and Cancelled issues stay on their historical parents.

**Epic lifecycle (Jim, November 21, 2026):** `Scoping → Ready → In Progress → Done`.
- A new product outcome starts as a LIFE Epic in Scoping. Children are written only while the Epic is in Scoping.
- **Feasibility first:** every Epic proves itself before any build ticket runs. Its first children carry the label `research`: read-only checks that write nothing but comments on their own ticket, with Jim must do first `None`. One verdict ticket per Epic carries the label `verdict`, is blocked by all of them, and makes bounded scoping writes only: it posts GO, GO WITH CHANGES or NO-GO on the Epic, rewrites the descriptions, labels and Jim must do first of that Epic's own children, and adds a Feasibility section to that Epic's Notion spec. It changes no code, runtime, workflow, credential or other Notion page, never drops or narrows scope, and never moves a ticket's status. Moving research and verdict tickets to Agent Go is Jim's approval of exactly those writes; they may run while the Epic is in Scoping; nothing else may. Every build ticket is blocked by its Epic's verdict.
- Each Epic links one Notion spec page for long rationale. Tickets stay self-contained: an agent builds from the ticket alone.
- **Claude writes, ChatGPT reviews:** Claude writes the Epic's children; ChatGPT reviews each against this section and §4 before the Epic moves to Ready.
- Every child carries exactly one readiness label:
  - `ready`: no unchecked Jim must do first item and no open scoping. Order between tickets is carried only by `Blocks` links: a `ready` ticket with an unfinished predecessor waits (Pick skips it; the release check refuses it);
  - `needs-prep`: engineering scoping is still open, with no decision needed from Jim;
  - `needs-jim`: blocked on a credential, host, spend or decision from Jim.

  Only `ready` tickets move to Agent Go.
- An Epic is Ready when every child has its label and its Jim must do first list. An approval that depends on an earlier ticket's result goes in the later ticket's list and is cleared between waves, never mid-ticket.
- Discovery answers go in the Epic's Notion spec or its comments, and are read back before the next question.

## 8. Locked architecture

LIFE OS is one platform with one live implementation per behavior. Flows read as `Acquire → Normalize → Decide → Persist → Present`.
- No framework, dispatcher, adapter layer or shared abstraction for a single consumer; a shared primitive needs two real consumers.
- No second scheduler service, orchestrator, datastore or source of truth without Jim.
- When a replacement works, remove the old path in the same change. Git is the archive.

GitHub Actions in this repository are the only scheduled executor, and the Production Contract approves each schedule. Each scheduled workload has:
- one owner and exactly one writer per target;
- declared dependencies, a provider budget, bounded retries and idempotency;
- a checkpoint with missed- and duplicate-run recovery;
- complete, degraded and incomplete receipts, and a rollback.

V7 must run with ChatGPT unavailable (D54). OpenClaw and Hermes run on the Oracle VM; Mac apps are thin clients.

**Debug in order:** `trigger → runtime started → code ran → source reached → state persisted → read back`.
- One retry per failure boundary; a second failure stops for diagnosis.
- More than two cycles or about ten minutes on a simple operation means challenge the design instead of adding machinery.
- Prefer subtraction over repair.

A request that breaks these rules stops with `ARCHITECTURAL DRIFT DETECTED — NO MUTATION`.

## 9. Proof and tests

- **Prove deterministically:** a fixture or clock test, or a replay of one recorded real input. Code is never accepted on a live run alone.
- **Natural runs, one rule:** a natural run gates Done only when the ticket's Done when names it (a cutover, a soak or a measurement ticket); then that many runs are part of the proof. Otherwise a live, scheduled or benchmark run never gates Done, and a fault it shows is filed as a Bug under LIFE-615.
- **Fix on the real failing shape.** A defect seen live is fixed and proved on that input, or on a fixture that keeps its structure, through the real path. For Jobs qualification: real job description → requirement rows → non-null Fit → correct disposition.
- **Runtime-facing changes:** prove the production entry point calls the change, its inputs are loaded, the happy path completes, missing input fails closed, writes go through the existing owner, and read-back is wired.
- **Jobs increments:** test toward the future source count before the pattern is copied (build at one, test toward many).
- **Fail closed:** a full result or an error, never a partial one. Dry run is the default; writes need `--live`.
- **Keep the suite fast:** under 90 seconds. Delete obsolete tests with the code they covered.
- **Net size:** `python -m lifeos.platform.net_size` in every PR.
- **Decision numbers:** only from `python -m lifeos.sources.devtools.decisions claim`.

## 10. Exceptions in force

- **V7 modernization consolidation exception (Jim, November 18, 2026; LIFE-834), applied as the fresh start (Jim, November 21, 2026, chat).** Jim approved cancelling every open ticket under the old initiatives (Epics LIFE-686, LIFE-763, LIFE-791, LIFE-809 and their open children) and the open LIFETMP holds. Their replacements are Epics LIFE-846 (Governance), LIFE-847 (Modernization) and LIFE-848 (Unified AI). Done and Cancelled tickets keep their parents, keys, status, history and evidence: none is reopened, deleted, reparented or rewritten, and the new tickets reuse their evidence (§7, Reuse). Any further reparenting or Epic cancellation needs Jim's yes on the exact list in chat.
- **Freezes and STOP gates lifted (Jim, November 21, 2026, 12:17 AM CT).** The LIFE-686 freeze and all STOP blocks are removed. Mac Claude Code is the exclusive writer of `hourly.yml` while LIFE-854 or LIFE-856 is open.

## 11. Read only when needed

- `docs/DECISIONS.md`: only when a ticket cites a D-number. Use `python -m lifeos.sources.devtools.decisions show D<n>` and never read the whole file.
- `docs/agent-reference.md`: the code map, commands and the nine epic delivery rules.
- `docs/archive/`: history, no authority.
