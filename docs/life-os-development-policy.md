# LIFE OS Development Policy

**Owner:** Jim Markunas. This one file is the whole policy. Change it by PR with Jim's yes. Edit a rule in place: replace it, never append a dated amendment. Old text goes to [docs/archive/](archive/development-policy-2026-11-20.md), which has no authority and is not read by default. The file stays under 15 KB so an agent reads it once.

## 1. Authority

1. Jim's current instruction.
2. The Production Contract, for schedules and runtime.
3. This policy, for how development works.
4. Notion LIFE OS Platform canon, for architecture.
5. Jira `LIFE` for product priority, delivery status, blockers and order. The `LOS` project is retired (Jim, Nov 21, 2026): history only. It stays, read-only.
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

- **Jim starts every agent run.** No scheduler, timer, poller, `/loop` or dispatcher may start or wake an agent outside a live session; inside one, it may watch its own CI and runs. A Jira status change starts nothing.
- **Once started, an agent runs end to end:** finish the ticket, then pull the next, until the queue is empty (`NO_ELIGIBLE_WORK`) or a ticket truly needs Jim.
- **Pick:** the ticket Jim named, else the top leaf of `project = LIFE AND status = "Agent Go" AND issuetype != Epic ORDER BY priority DESC, Rank ASC`. Skip a ticket In Progress (another session has it) or with an unfinished `Blocks` predecessor. Agents never reorder or reprioritize. Several sessions (OC and MC, CLAUDE.md) may pull in parallel; one writer per branch and per mutation surface.
- **Claim:** move the ticket to In Progress (transition ID 14). The status is the claim; no comment.
- **Build:** one branch `life/LIFE-nnn/<slug>`, one PR titled `[LIFE-nnn] <outcome>`. Tickets touching the same files may share one PR. Smallest change that delivers the outcome; remove the path it replaces.
- **Test:** `sh tools/preflight.sh` before every push. CI is the full gate: fix once if red.
- **Land:** auto-merge on same-head green CI. No reviewer is required unless branch protection or the ticket says so; never invent an approval.
- **Close when shipped:** a build ticket is Done once it is merged on same-head green CI, live, and first-proved. A merged PR on green is not Done by itself. A check that only waits (natural runs, a soak, a scheduled time, Jim's UAT) becomes its own Task under LIFE-615: `UAT for LIFE-nnn: <check> (not before <time> CT)`, label `uat` plus `ready` or `needs-jim`, Agent Go, with the build ticket, verifier, Not before (CT), steps, Done when and "If it fails: file a Bug under LIFE-615". The build ticket names it; a successor starts once its predecessor is Done.
- **Close:** `python -m lifeos.jira.closeout KEY... --pr N --sha S --run R --proof KEY=text --live` posts the one record (PR, SHA, CI run, outcome proof per Done when line) and moves the ticket to Done. Jim reopens it or files a Bug.
- **Close a `research` ticket:** no PR, so no closeout script. Post one findings comment that answers every Done when line (UNCLEAR or UNVERIFIED with the reason is an answer), then move it to Done with transition 15. A `verdict` ticket closes the same way after its authorized writes are read back.
- **Human UAT:** only with label `needs-uat` or when Jim is the real user of the outcome. Stop at Human UAT (transition 19) with exact click-through steps for Jim; Jim's check moves it to Done.
- **Schedules:** adding, moving or changing the cadence of any scheduled workload is a Production Contract change: it is in the ticket's Jim must do first, per workload, before release.
- **Scope:** never change or drop a ticket's stated scope, or swap a stated fix for a workaround, without asking Jim. Optional improvements become a proposal, not extra work.

## 4. Jim must do first (the release rule)

Every LIFE ticket includes **Jim must do first** checkbox items for owner-only: secrets/Bitwarden, permissions/sign-in, spend/paid models, Mac/Oracle host steps, product decisions, Notion wireframe approval, and UAT setup. `None` means no prerequisites.

- **Chat approval:** Jim's explicit ChatGPT instruction naming a LIFE ticket and approved scope is sufficient human authorization. ChatGPT records it in the Jira description, checks only proven owner items, verifies live readiness/dependencies/verdict, and moves an eligible ticket to `Agent Go`; no repeat approval. Ambiguous consent does not count; it never waives safety/feasibility gates or starts an agent.
- **Release:** A ticket with an unchecked item or no section does not move to Agent Go. `lifeos.jira.promote`, auto-release, and `python -m lifeos.sources.devtools.readiness` enforce this.
- **Agent Go is the approval** of the written scope; agents do not re-ask.
- **Check before claiming:** before transition 14, list every §5 item and host step the ticket will hit. If one is neither checked nor a standing approval (§5), do not claim: post one approval table, label `needs-jim`, take the next ticket. Found mid-run: take the ticket's pre-made default (§12) and keep building; with none, file the `dry-run-miss` Bug (§12) and take the next ticket instead of stopping to ask Jim. The label `dry-run-green` is checked at claim time (§12).

## 5. What always stops for Jim

These four need Jim's yes. Put them in **Jim must do first** whenever they are known. `tools/workflow_gate.py`, run by preflight, stops on them:

1. Adding or widening a workflow `permissions` block or any write permission.
2. Adding, moving or exposing a credential. Secrets live in a secrets manager (Bitwarden today; Doppler only if Jim picks it), never in the repo, Jira or chat. GitHub's secret store may hold only one bootstrap token per secrets manager (today `LIFEOS_BWS_RUNTIME_TOKEN`); adding one is still Jim's decision (Jim, Nov 21, 2026).
3. Deleting a GitHub value or production data.
4. Spend or a paid model beyond the ticket's budget.

**Standing approvals (Jim, Nov 22, 2026):** no stop for a job reusing a secret or Bitwarden name its workflow already loads; deploying or restarting a LIFE OS service on Oracle with a recorded rollback; a root read of config showing names only; a read-only grant expiring within 90 days; a workflow edit widening no permission. Name the class in the PR's `[approvals]` line. Everything else in this list still stops.

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

Keep the description under 4,000 characters; one ticket is one PR. Reference secrets by identifier only. Link the Notion requirement instead of copying it. The description beats comments: a scope change is an edit to the description, never a comment alone.

**UI first:** for new or changed UI, show the smallest working preview in the real surface (with synthetic data where live data is not authorized), and get Jim's acceptance before expanding the backend. For changed results in an existing UI, show current versus intended output. A UI-free change names its downstream outcome and proof.

**Hierarchy:** LIFE Epic → Task → Sub-task.
- Every ticket sits under an epic; Bugs go under LIFE-615.
- Every ticket has exactly one Component (its owning OS) and the matching Fix Version `<component> X.Y.Z`. These are filled automatically (LIFE-741). Agents never invent releases.
- Done and Cancelled issues keep their parents.

**Epic lifecycle (Jim, Nov 21, 2026):** `Scoping → Ready → In Progress → Done`.
- A new product outcome starts as a LIFE Epic in Scoping. Children are written only while the Epic is in Scoping.
- **Feasibility first, new Epics only:** a new Epic (only when Jim asks) first runs `research` children (read-only, comments only) and one `verdict` ticket blocked by them, whose only writes are the GO / NO-GO comment, its own children's descriptions, labels and Jim must do first, and a Feasibility section on its spec. Build tickets wait for the verdict. A change inside an existing Epic goes straight to a build ticket.
- **One ticket at a time** per session: finish (Done, Human UAT or `needs-jim`) before claiming the next.
- Each Epic links one Notion spec page for long rationale. Tickets stay self-contained: an agent builds from the ticket alone.
- **Claude writes, ChatGPT reviews:** Claude writes the Epic's children; ChatGPT reviews each against this section and §4 (`docs/jira/review-checklist.md`) before it moves to Ready.
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

V7 must run with ChatGPT unavailable (D54). OpenClaw and Hermes run on the Oracle VM; Mac apps are thin clients. **Oracle access:** agents use `ssh opc@lifeos-edge-01`; `sudo -u ubuntu` reads are read-only; root only for a step the ticket approves; nothing else changes.

**Debug in order:** `trigger → runtime started → code ran → source reached → state persisted → read back`.
- One retry per failure boundary; a second failure stops for diagnosis.
- Two cycles or ten minutes on a simple operation: challenge the design, don't add machinery.
- Prefer subtraction over repair.

A request that breaks these rules stops with `ARCHITECTURAL DRIFT DETECTED — NO MUTATION`.

## 9. Proof and tests

- **Prove deterministically:** a fixture or clock test, or a replay of one recorded real input; never on a live run alone.
- **Natural runs, one rule:** a natural run never gates a build ticket's Done; its UAT ticket checks it, and a fault is filed as a Bug under LIFE-615.
- **Fix on the real failing shape.** A defect seen live is fixed and proved on that input or a structure-keeping fixture, through the real path. For Jobs qualification: real job description → requirement rows → non-null Fit → correct disposition.
- **Runtime-facing changes:** prove the production entry point calls the change, its inputs load, the happy path completes, missing input fails closed, writes use the existing owner, and read-back is wired.
- **Jobs increments:** test for the future source count before copying a pattern.
- **Fail closed:** a full result or an error, never a partial one. Dry run is the default; writes need `--live`.
- **Keep the suite under 90 seconds;** delete tests with the code they covered.
- **Full width (D16.1):** a page V7 creates under a page uses `template=notion.FULL_WIDTH`; a repo test enforces it.
- **Net size:** `python -m lifeos.platform.net_size` in every PR.
- **Decision numbers:** only from `python -m lifeos.sources.devtools.decisions claim`.

## 10. Exceptions in force

- **V7 modernization consolidation exception (Jim, Nov 18 and 21, 2026):** applied; old open tickets were cancelled and replaced by Epics LIFE-846, LIFE-847 and LIFE-848, history untouched. Further reparenting or Epic cancellation needs Jim's yes on the exact list.
- **Freezes and STOP gates lifted (Jim, Nov 21, 2026, 12:17 AM CT).** All freezes and STOP blocks removed. The agent holding LIFE-856 In Progress is the sole writer of `hourly.yml`.

## 11. Read only when needed

- `docs/DECISIONS.md`: only when a ticket cites a D-number. Use `python -m lifeos.sources.devtools.decisions show D<n>` and never read the whole file.
- `docs/agent-reference.md`: the code map, commands and epic delivery rules.
- `docs/archive/`: history, no authority.

## 12. Dry run gate (scoping, before Agent Go; Jim, Nov 25 2026, LIFE-986)

Blockers found mid-run (a secret in the wrong Bitwarden project, a delete the provider refuses, a tool not loaded, an action the agent's own rules stop, a target out of reach) are found before the build instead.

1. **Every new ticket carries two sections** (template: `docs/templates/ticket.md`): **Dry run**, a checklist of everything the build touches, and **Decisions pre-made**, an "if X, do Y" default for every fork. Jim approves the defaults once, when he releases the ticket.
2. **The executing agent runs the dry run** on the machine and identity that will do the build. It is read-only: no write, merge, deploy or delete. It checks: every named **secret** exists under its exact name in the project the runner can read (name only, never the value); every **write or delete** is allowed for that identity (provider docs or a read-only check; if that cannot be confirmed without writing, it is a Jim step); every **tool**, connector and CLI is loaded in that agent's session; every action the agent's own **permission rules or safety checks** will stop (for example Keychain reads and deletes) is a pre-approved rule or a Jim step; the **target** is reachable within the approved scope on current data (if not, the decision goes into Decisions pre-made); every **wait** (natural runs, soaks, dates) is already its own `UAT for LIFE-nnn` ticket under LIFE-615.
3. **One `Dry run` comment** per ticket: a table of check, result and any human step. Every human step moves into the ticket's **Jim must do first**.
4. **Jim batches:** he does the Jim must do first list in one go, then says `release LIFE-nnn`. That is his only involvement before the build. He may say `dry run 856 877 981`: the agent dry-runs them in one session and posts one combined list of human steps, as an Agent Inbox item and on each ticket.
5. **Gate:** a ticket without a green Dry run comment and the label `dry-run-green` cannot be claimed (checked at claim time). Tickets already in Agent Go on Nov 25 2026 are grandfathered. A blocker the dry run missed: take the pre-made default if there is one; otherwise file a Bug under LIFE-615 labelled `dry-run-miss` naming the missed check, and move to the next ticket instead of stopping to ask Jim.
6. **One session, one outcome:** a build ticket ends Done (merged, live, first proof) or with a Bug filed. "Left open for..." is not allowed; every leftover becomes its own UAT or Bug ticket. Each session starts by reading Jira state, not its memory of earlier sessions.
