# LIFE OS V7 — Claude Code entrypoint

Read [AGENTS.md](AGENTS.md) and the [canonical LIFE OS Development Policy](docs/life-os-development-policy.md), especially **Deterministic agent work selection** and **Jira-first development lifecycle**, before starting development.

**Route first:** classify the task by the policy's **Adaptive Execution** lane (FAST, STANDARD, ENGINEERING, PROTECTED) before any Jira or GitHub read; Fast Start below is the ENGINEERING bootstrap.

**Fast Start:** Use the 60-second routine bootstrap target in [AGENTS.md](AGENTS.md). Start with `START_EPOCH=$(date -u +%s)`, retain verified task context on warm resumes, fetch minimal Jira fields and V7 GitHub SHA/changed diff, and parallelize independent reads. After the essential reads, report elapsed seconds and number of remote reads in the existing status; do not rerun full Jira/GitHub history or change acceptance/permission gates.


**Deadline Mode:** For any user deadline, first run `TZ=America/Chicago date '+%Y-%m-%d %H:%M:%S %Z'; date -u +%s` in your real execution shell. Derive and retain an absolute `LIFEOS_DEADLINE_EPOCH`, recheck remaining seconds before/after each material operation, automatically cut optional work at 50%, freeze scope at 75%, and prioritize authorized shipping plus read-back at 90%. Never reset the deadline, claim a PR is delivered product, or bypass security/merge/model-spend approvals. Follow the exact V7 policy section **Deadline Mode**.

As an authorized coding session, self-resume the active claimed `LIFE` issue or self-pull the next *eligible* live Jira issue for your approved lane after accepted completion. Verify dependencies, owner, readiness and order rather than accepting creation-date Rank or a stale roadmap. If Jira is unavailable or the queue lacks authoritative readiness, return `QUEUE_NOT_READY` with the exact gap. A verified Tech Lead handoff can authorize a specific one-off task only; it cannot replace autonomous Jira access.

Obey the policy's repository affinity, one-writer, permission, acceptance and Jim-approval gates. V7 is the active repository; V1/V2 are read-only references and have no live development-policy authority. This pointer does not grant Jira write access, OpenClaw permissions or merge authority.
