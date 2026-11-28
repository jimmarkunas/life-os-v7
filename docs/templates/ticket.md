# Ticket template (LIFE-986; policy §7 and §12)

Claude chat and the agents write every new ticket in this shape. One ticket is one PR and stays under 4,000 characters; link the Notion spec instead of copying it.

## Why

One or two sentences: the outcome and who asked (Jim's words and CT time if he gave them).

## Exact targets

Files, pages, fields, numbers. What changes, in the repo's own words.

## Paths

One line: the repo paths this ticket will touch, comma separated (policy section 3; `docs/policy/agent-coordination.md`). An agent about to start a ticket whose paths overlap one that is In Progress waits, or is sequenced by Claude.

## Dry run

The checklist of everything the build will touch. The executing agent runs it read-only on the machine and identity that will do the build, then posts one `Dry run` comment (check, result, human step) and adds the label `dry-run-green`.

| Check | Expected |
| --- | --- |
| Every secret the ticket names exists under its exact name, in the project the runner can read (name only) | yes |
| Every write or delete is allowed for that identity (provider docs or a read-only check) | yes, or a Jim step |
| Every tool, connector and CLI the ticket needs is loaded in that agent's session | yes |
| Every action the agent's own permission rules or safety checks will stop is a pre-approved rule or a Jim step | yes |
| The target is reachable within the approved scope, measured on current data | yes, or a decision below |
| Every wait (natural runs, soaks, dates) is its own `UAT for LIFE-nnn` ticket under LIFE-615 | yes |

## Decisions pre-made

"If X, do Y" for every fork. Jim approves them once, when he releases the ticket.

## Jim must do first

- [ ] Each owner-only step (secret, permission, sign-in, spend, host step, decision, Notion wireframe). `None` if there are none.

Jim does them in one batch, then says `release LIFE-nnn`.

## Experimental package (only for POC work; policy section 7, `docs/policy/integrated-delivery.md`)

One Task and one PR for the whole outcome. Label it `package`. Sections, each non-empty: **Outcome**, **Candidate POCs**, **Integration**, **Deployment**, **Rollback**, **Acceptance matrix** (frozen criteria for outcome, security, cost and rollback), **Budget** (time, calls, spend), **Alternatives** (approved fallbacks), **Approval** (one line per protected act: `act: target ...; budget ...`). Agent Go approves this envelope, not an unconditional production GO.

## Done when

One `Given ... when ... then ...` line or bullet per behavior. A tick without cited proof is not Done.

## Not in scope

## Rollback

## Executing agent

`OC`, `MC`, or first to claim.

## Verifier

Who verifies, by reading or clicking, never by running commands.

Component and Fix Version are filled automatically (policy §7).
