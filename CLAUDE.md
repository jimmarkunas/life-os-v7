# LIFE OS V7: Claude Code entrypoint

Read [AGENTS.md](AGENTS.md) and follow it. The [Development Policy](docs/life-os-development-policy.md) is the whole rule set, in one file; read the section a task needs.

- **Times for Jim:** US Central Time only, labeled CT, 12-hour (for example 4:34 PM CT). Never show UTC; convert log, API and cron times first.
- **Runs:** Jim starts each session. Once started, finish the ticket, then pull the next `Agent Go` ticket yourself until the queue is empty. Never add `/loop`, timers or scheduled Claude invocations, or anything that starts or wakes an agent after the session ends. Inside a session that is already running, you may watch your own CI and workflow runs.
- **Approvals:** moving a ticket to Agent Go is the approval. Merge on same-head green CI, prove every Done when line on the delivered outcome (Agent UAT), then move the ticket to Done yourself (or to Human UAT when the ticket names Jim as verifier); Jim reopens it or files a Bug. Anything only Jim can do belongs in the ticket's **Jim must do first** section before release.
