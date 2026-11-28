# Epic review checklist (LIFE-851)

ChatGPT reviews every child of an Epic against this list, then records one comment on the Epic in Jira. The Epic moves to Ready only after a current `ChatGPT review: pass`. Policy: `docs/life-os-development-policy.md` sections 4 and 7.

## Each child has

1. Outcome, and Before with the evidence it reuses.
2. Exact targets (files or systems) and numbered Steps.
3. Done when: runnable outcome proof, not a plan.
4. Jim must do first: every owner item, including schedule and protected approvals, as checkboxes (`None` if none); any inherited approval named; protected actions (policy section 5) listed.
5. Blockers as `Blocks` links; one readiness label (`ready`, `needs-prep` or `needs-jim`).
6. Not in scope, and who verifies.
7. Source authority named: one canonical owner per Notion requirement.

8. For experimental work, one `package` Task with every package section (policy section 7), not separate research and verdict tickets; each protected act names its target and budget.

## Each child must not have

- Plan-shaped or gate-filled text (a placeholder that only satisfies a check).
- Something Jim must do hidden in Steps.
- A second authority beside an existing one.

## The record

One Epic comment. The first line is `ChatGPT review: pass` or `ChatGPT review: changes`, then one result line per child key (`LIFE-nnn: pass` or the change needed). Use the connected Atlassian app. If it cannot write, Claude Code records the review verbatim and says who wrote it.

`python -m lifeos.sources.devtools.readiness epic-review-key LIFE-nnn` refuses a Ready Epic with no pass (`missing`), a newest review of `changes` (`failed`) or a child edited after the newest pass (`stale`). No scheduler runs it.
