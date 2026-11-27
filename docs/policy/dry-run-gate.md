# Dry run gate (policy section 12)

Moved here from `docs/life-os-development-policy.md` section 12 with no change in meaning. Read it when scoping or releasing a ticket, or when a ticket lacks the `dry-run-green` label.

## 12. Dry run gate (scoping, before Agent Go; Jim, Nov 25 2026, LIFE-986)

Blockers found mid-run (a secret in the wrong Bitwarden project, a delete the provider refuses, a tool not loaded, an action the agent's own rules stop, a target out of reach) are found before the build.

1. **Every new ticket carries two sections** (template: `docs/templates/ticket.md`): **Dry run**, a checklist of everything the build touches, and **Decisions pre-made**, an "if X, do Y" default for every fork. Jim approves the defaults once, when he releases the ticket.
2. **The executing agent runs the dry run** on the machine and identity that will do the build. It is read-only: no write, merge, deploy or delete. It checks: every named **secret** exists under its exact name in the project the runner can read (name only, never the value); every **write or delete** is allowed for that identity (provider docs or a read-only check; if that cannot be confirmed without writing, it is a Jim step); every **tool**, connector and CLI is loaded in that agent's session; every action the agent's own **permission rules or safety checks** will stop (for example Keychain reads and deletes) is a pre-approved rule or a Jim step; the **target** is reachable within the approved scope on current data (if not, the decision goes into Decisions pre-made); every **wait** (natural runs, soaks, dates) is already its own `UAT for LIFE-nnn` ticket under LIFE-615.
3. **One `Dry run` comment** per ticket: a table of check, result and any human step. Every human step moves into the ticket's **Jim must do first**.
4. **Jim batches:** he does the Jim must do first list in one go, then says `release LIFE-nnn`. That is his only involvement before the build. He may say `dry run 856 877 981`: the agent dry-runs them in one session and posts one combined list of human steps, as an Agent Inbox item and on each ticket.
5. **Gate:** a ticket without a green Dry run comment and the label `dry-run-green` cannot be claimed. Tickets already in Agent Go on Nov 25 2026 are grandfathered. A blocker the dry run missed: take the pre-made default if there is one; otherwise file a Bug under LIFE-615 labelled `dry-run-miss` naming the missed check, and move to the next ticket instead of stopping to ask Jim.
6. **One session, one outcome:** a build ticket ends Done (merged, live, first proof) or with a Bug filed. "Left open for..." is not allowed; every leftover becomes its own UAT or Bug ticket. Each session starts by reading Jira state, not its memory of earlier sessions.
