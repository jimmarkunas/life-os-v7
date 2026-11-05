# LIFE OS delivery flow: why it hurts and a simpler one (PROPOSAL)

Status: **PROPOSAL for Jim. Nothing in this document is in force.** The draft PR carrying it changes no Jira setting, merges nothing and is not approved. Prepared November 5, 2026 (CT) from a read-only review of Jira LIFE, V7 PRs #740–756, the policy parts and the Hermes tickets.

Plain-language summary: in one week, three small Hermes tests took 37 hours and at least five approvals, a tiny feature needed four tickets, and two tickets were released that could not run. The cause is not slow agents. The process asks you to approve *actions* instead of *outcomes*, never corrects its own paperwork, and lets a ticket be "released" before it can run. The fix is one ticket per outcome, one approval, one receipt, one acceptance, plus a check that refuses unrunnable tickets.

---

## A. Findings: eight root causes

All counts below were read from Jira on Nov 5, 2026 unless marked UNVERIFIED. Times are CT.

**1. Approval is per action, not per outcome.**
- LIFE-281 (M9 tests) was approved Nov 4 at 1:12 AM CT as six named tests (comment 11727). Its last receipt landed Nov 5 at 2:28 PM CT: **37 hours 16 minutes**. It is still in Agent Go.
- It carries 9 comments: 6 control comments (11724 not authorized, 11727 approve M9a–f, 11739 receipt standard, 11745 closeout change, 11884 re-scope to M9a/e/f, 11889 "Run M9f") and 3 receipts (11891, 11898, 11899).
- You gave at least **5** go/approval chat messages ("Approve all now", "approve M9a to M9f", the re-scope, "Run M9f", "Let's go!"). Whether M9e had its own "go" is UNVERIFIED.
- Result: M9a, M9e, M9f ran and passed (with UNVERIFIED gaps), M9b/c/g were skipped, M9d became obsolete. Three small tests, five approvals.
- Instruction conflict: "skip disk-full" versus "run M9f" (M9f *is* the disk-full test) held the work until 11889.

**2. Tickets are never kept true.**
- LIFE-304: title and all 5 Given/When/Then lines still say "add Qwen through OpenRouter". The work became a model-route parity sign-off (3 scope changes: comments 11841, 11868, 11893). The description was never edited. It went To Do → Done in one jump at 2:10 PM CT Nov 5, 12 hours 39 minutes after creation, so its status told nothing in between. (The brief said "a day"; the real figure is 12h39m.)
- LIFE-293: In Progress, every acceptance box unchecked, although comment 11836 says all done. Its build follow-up LIFE-307 was created when you said "changes: build the v7 shit" (Human UAT → In Progress, 2:33 AM CT Nov 5). 9 comments, 6 status changes.
- LIFE-281 GWT-M9-02 still cites an expiry that was removed.
- Roughly **64% of LIFE-281's text is ceremony** (receipt format, STOP lists, approval boilerplate), not outcome.

**3. "Agent Go" does not mean "can run".**
- LIFE-306 (secrets broker) was released twice (2:18 AM and 2:17 PM CT Nov 5) and parked once (3:48 AM CT). 14 comments, 4 status changes.
- Blocker: the free Bitwarden plan has no free project or machine-account slot (comment 11856). The second release (11897) itself says nothing can be built. Your reply 11900 said "revised: C" and "close ticket", which conflict, so it was held.
- `lifeos/readiness.py` checks origin, lane, blockers, authorization, permissions and Given/When/Then. It has **no check** for human prerequisites, plan or capacity limits, or open decisions, so it passed a ticket that could not run.

**4. Too many tickets and stages for a small feature.**
- LIFE-307 (one V7 stage posting one Buzz request): the build itself took about 9 minutes (Agent Go 2:32:39 AM → Agent UAT 2:41:22 AM CT Nov 5), but it needed LIFE-293 (design), LIFE-306 (broker, bypassed), LIFE-298 (key exposure) and 12 hours later it was still not live; PR #756 was open.
- Your touches on that one feature: rule approval (11845), key generation, adding the key to the Buzz community, an @mention to enter the channel (still open), allow-list approval (11895), running a host script, merging, and the first live "go": **7–8 touches**.
- The Hermes chain LIFE-270–287 is 18 tickets: 11 Done, 4 To Do (coordination tickets LIFE-271–274), 1 Agent Go (281), 2 Cancelled without ever running (282, 283). Epic LIFE-269 is still To Do.
- Workflow: from Agent Go there are **11 transitions to 8 targets**; three lead to In Progress ("Move to In Progress", "Start authorized work", "Start work (authorization receipt required)") and two to blocked ("Move to blocked", "Report blocker"). Duplicates exist only to carry different rules text.

**5. You are the relay between the agent and the host.**
- The permission classifier denied agent host actions 4 times (comments 11781, 11819, 11860, 11868), so you ran about **10–11 host scripts** across these tickets and pasted output back.
- Terminal pastes were cut off or ended early in at least **4** cases (11822, 11871, 11891, 11898; full for M9e). Each cut-off costs another round.

**6. Competing sources of truth.**
- Merge rule: your hard gate says agents never merge. AGENTS.md step 5 said "merge when same-head CI is green" while its Reporting section says "do not merge"; policy Part 07 items 3/3a authorize Claude Code to merge its own PR. Three texts, two answers.
- The Hermes Notion spec is 179K characters and still describes sunset/teardown, while the Nov 5 decision made Hermes permanent.
- 14 PRs (#740–756) in two days; **7** (#741, #743, #744, #746, #749, #752, #754) changed process, policy or guardrails, and #740 is a routing index. The policy parts total about 127 KB, rewritten in place each time.

**7. Hand-typed times.**
- Agents wrote times without checking a clock: 4 correction comments (11849, 11850, 11851, 11853) fixed **7** wrongly timed comments on LIFE-307/293/306/304, and 11886 fixed 11800 on LIFE-298. The rule is right; the mechanism (agent types a time) is the problem.

**8. One Jira identity for everyone.**
- Every comment on these tickets shows the author "Jim Markunas", including agent receipts, because agents use your credentials. An approval and an agent claim cannot be told apart by author. Agent identities in Jira were already raised in PR #752.

---

## B. Proposed delivery flow: one ticket, one approval, one receipt, one acceptance

**The flow (four moments):**

1. **Ticket (agent drafts, short template).** One *outcome ticket* per feature, with only these parts:
   - *Outcome* (one sentence).
   - *Done when* (at most 5 checkable lines).
   - *Needs from Jim* (each item with how it is proven done, e.g. "Bitwarden project slot exists: plan page shows it").
   - *Protected steps* (each with its rollback).
   - *Not in scope*.
2. **ONE approval by Jim**, before work starts: "Approve LIFE-nnn." It covers the Done-when list **and** the named protected steps. Scope changes edit the ticket (rule D); they do not need a new approval comment unless they add or widen a protected step.
3. **Agent executes to completion** with no further go-aheads: build, tests, PR, CI repairs, running everything it is allowed to run. Protected host steps run through the approved script pack (rule E), which is the existing named human approval, kept.
4. **ONE receipt, ONE acceptance.** The agent posts one receipt in the existing four-line format, comparing each Done-when line to its proof. Jim accepts once, or the ticket pre-delegates closeout and the agent closes on read-back evidence (already allowed by Part 07 item 4).

**What is deleted or merged:**

| Today | Proposed |
|---|---|
| Design ticket + build ticket + prerequisite tickets + coordination tickets for one feature | One outcome ticket. Prerequisites become "Needs from Jim" lines, not tickets. No coordination tickets. |
| Per-test, per-operation approval comments (M9a…M9f) | One approval of the Done-when list |
| Per-ticket STOP lists and boilerplate blocks | The single global stop list in AGENTS.md (one place) |
| Three "start" transitions, two "blocked" transitions | One of each (F1) |
| Agent UAT then Human UAT, plus "Complete without Human UAT" | One "Ready for Jim" state (F2, optional); closeout either by Jim or pre-delegated |
| Receipt written differently per ticket | One four-line receipt, compared to Done-when |
| Agent types the time | Time = the comment's own Jira timestamp; agents write no clock times |

**What is kept (unchanged gates):** agents never merge; protected host operations keep a named human approval and rollback; no secrets in chat, Jira, GitHub or logs; Jira is the status source of truth; no timers or watchers; times to Jim in CT.

---

## C. Readiness rule: do not release what cannot run

Add to the existing offline validator (`python -m lifeos.readiness issue`) two lists on the normalized issue record:

- `needs`: each row `{what, proof, status}`; `status` must be `done` and `proof` non-empty.
- `decisions`: same shape; an unresolved decision is a row that is not `done`.

Any open row makes the validator refuse with `prerequisite_open` or `decision_open`, so the ticket **cannot move to Agent Go** and agents cannot claim it. This is implemented in this draft PR with tests (checked when the lists are present, so existing tickets keep working). Under this rule LIFE-306 would have stopped at the first release: "Bitwarden project slot: proof = plan page shows a free slot" would be open, and "route decision C vs close" would be an open decision.

Who fills the lists: the agent drafting the ticket (it can read what a plan allows). You only see "Needs from Jim" lines that are really yours.

---

## D. Keeping a ticket's text true

- **Who updates:** the agent doing the work, in the same step as the change. If the outcome changes, it edits the title and Done-when lines first, then posts the one-line reason. Comments are history, not the spec.
- **When:** before the next receipt. A receipt that does not match Done-when line by line is refused by review.
- **Superseded tickets:** Cancelled at once with "Superseded by LIFE-x" in the description and a link; never left To Do or Agent Go. (LIFE-282/283 were handled this way correctly; LIFE-271–274 and LIFE-269 were not.)
- **Epics:** the agent that closes the last child closes the epic.
- **Coordination tickets:** not created. A dependency is a link.
- **Specs outside Jira:** when a decision changes a Notion spec, the ticket links the spec and marks it stale in the same edit (Hermes spec today is stale).

---

## E. Cutting your copy-paste without removing any gate

**Option 1 (recommended): approved script pack with built-in read-back.**
- The agent prepares the host change as a script in a pack and prints its sha256.
- You approve by replying `run LIFE-nnn <first 8 characters of sha256>`.
- You run **one fixed command** on the host. The runner checks the hash prefix, runs with a rollback trap, writes names-and-counts read-back to a file the agent reads, and prints a single `RESULT ok|fail` line. Nothing long to paste, nothing to truncate. No secret ever prints.
- The runner is itself a new protected host component, so it needs your explicit approval and a rollback before use.

**Option 2: narrow sudoers allowlist** (as LIFE-306 D1=B discussed). Agent-run, fewest touches, but the largest standing permission; keep the allowlist to named commands.

**Option 3: status quo plus shorter output.** Scripts print only `RESULT` and write detail to a file you do not paste. Cheapest, smallest gain.

Estimated effect on a typical host change: from about 3–4 of your steps (approve, run, paste, paste again when cut off) to 2 (reply `run …`, run one command). UNVERIFIED: time saved per change.

---

## F. Changes

**Draft PR contents (not merged, marked PENDING):**
1. `AGENTS.md`: step 5 changed from "merge when CI is green" to "Jim merges" (pending your decision F5); a one-paragraph pointer to this document.
2. `docs/development-policy/07-bootstrap-bounded-autonomy.md`: items 3 and 3a marked "PENDING Jim's decision to retire", and a short PENDING paragraph for the flow. `manifest.json` gets a new hash and an amendment entry whose approval says PENDING (same pattern as PR #752).
3. `lifeos/readiness.py` and `tests/contracts/test_readiness.py`: the `needs` and `decisions` checks (rule C).
4. This document.

**Jira changes for you to approve one by one (nothing done yet):**
1. **Remove duplicate transitions.** Keep "Start work (authorization receipt required)" (14) and "Report blocker" (2); remove "Start authorized work" (6), "Move to In Progress" (16) and "Move to blocked" (20). Then fix the IDs the policy cites.
2. **Merge Agent UAT and Human UAT into "Ready for Jim"** (optional; keep Agent UAT if you prefer).
3. **Clean-up of stale tickets:** cancel LIFE-271, 272, 273, 274 (coordination); reconcile Epic LIFE-269; retitle LIFE-304 to what it actually delivered; close LIFE-293 as merged into LIFE-307; close LIFE-281 with a final receipt; close LIFE-306 or rebuild it as route C with `needs` filled.
4. **Separate agent Jira identity** (optional; cost UNVERIFIED), so approvals and agent claims differ by author.
5. **Merge wording:** confirm "agents never merge; Jim merges", and approve retiring Part 07 items 3/3a.
6. **Approve the script pack runner** (E, Option 1) as a protected host component, with its rollback.

---

## G. Before and after: LIFE-281 (M9a–M9f)

| | Today (measured) | Proposed |
|---|---|---|
| Approvals from you | at least 5 chat approvals | 1 |
| Comments on the ticket | 9 (6 control, 3 receipts) | 2–3 (approval, one receipt, optional close) |
| Host scripts you ran | 3, two pasted back cut off | 1 command, 0 pasted output |
| Elapsed | 37h 16m (still open) | not estimable; removes the waiting between approvals. UNVERIFIED |
| Conflicting instructions | 1 (11889) | 0, because Done-when names the tests once |
| Ticket text matching reality | no (GWT-M9-02 stale; 3 tests skipped, 1 obsolete) | yes, by rule D |

Proposed path: you approve "run M9a, M9e, M9f, skip the rest" once. The agent runs the pack, reads the result file, edits Done-when to what was run, posts one receipt, closes.

---

## What I read, what I could not read, and the three changes that help most

**Read:** V7 CLAUDE.md, AGENTS.md, the policy index and parts 04 and 07 in full, `manifest.json`, `lifeos/readiness.py` and its tests; Jira LIFE-281, 293, 298, 304, 306, 307 and the Hermes chain (comments, changelogs, transitions); PRs #740–756 (titles and counts); the Hermes Notion spec (size and staleness only).

**Could not read or not verified:** Notion LOS roadmap in full; LIFE-305; the full Hermes Notion spec text (179K characters; checked for staleness only); whether M9e had its own "go" (UNVERIFIED); host-side logs of your scripts; time saved by the script pack (UNVERIFIED); cost of a separate agent Jira user (UNVERIFIED).

**If you approve only three:**
1. **One approval per outcome** (rule B: one approval covers Done-when and named protected steps; scope changes edit the ticket).
2. **Readiness refuses open prerequisites and decisions** (rule C, already coded in this PR).
3. **Approved script pack with built-in read-back** (rule E, Option 1).

A cheap fourth if you want it: stop agents typing clock times (use the comment's Jira timestamp).
