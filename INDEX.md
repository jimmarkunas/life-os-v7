# LIFE OS global source index

Routing manifest for the global sources in this repository (LOS-74 decision Q11; LIFE-222). It says which file answers which question, what authority it has, who owns it and how to retrieve it. It contains no rules: every rule stays in the source it names, and the [Development Policy](docs/life-os-development-policy.md) decides precedence. GitHub `main` is canonical; record the commit SHA you read at task start (Q06).

Machine-readable twin: [`docs/index.json`](docs/index.json) (kept identical by a contract test). AGENTS.md is the master entrypoint; every other source is detail it routes to.

Project-specific context (requirements, workflows, configuration) is not here. It lives in the ChatGPT Project's Drive `PROJECT-INDEX.md` (LIFE-223); execution and status live in Jira `LIFE`, product decisions in Jira `LOS` (Q16).

## Global sources

| Path | Authority | Purpose | Owner | Retrieval |
|---|---|---|---|---|
| [`INDEX.md`](INDEX.md) | Routing only: no rule of its own | This manifest: which global source answers which question, who owns it and how to fetch it | Jim (change by reviewed PR; Q04) | Read first in a fresh session, then fetch only the rows the task needs |
| [`AGENTS.md`](AGENTS.md) | Bootstrap pointer, subordinate to the Development Policy | Agent start-up: the job, Jim must do first, what stops for Jim | Jim (change by reviewed PR; Q04) | Read on every coding task |
| [`CLAUDE.md`](CLAUDE.md) | Bootstrap pointer, subordinate to the Development Policy | Claude Code entrypoint: Central Time, end-to-end runs, approvals | Jim (change by reviewed PR; Q04) | Read on every Claude Code task |
| [`docs/life-os-development-policy.md`](docs/life-os-development-policy.md) | Canonical: the whole Development Policy (one file) | Authority, delivery, release rule, guardrails, privacy, tickets, architecture, proof | Jim (change by reviewed PR; Q04) | Read the section the task needs |
| [`docs/life-os-production-contract.md`](docs/life-os-production-contract.md) | Canonical: the Production Contract, for schedules and runtime (v2.15.19, moved unchanged from the V1 repository) | What may run, when, with which secrets and owners; each scheduled workload's section | Jim (change by reviewed PR; Q04) | Read the section for the workload a task changes |
| [`docs/agent-reference.md`](docs/agent-reference.md) | Reference: no rule of its own beyond its test-enforced list | Code map, commands, the nine epic delivery rules | Jim (change by reviewed PR; Q04) | When a task needs a command or the map |
| [`docs/jira/issue-schema.v1.json`](docs/jira/issue-schema.v1.json) | Derived: machine-readable form of the issue-writing contract; the policy part wins | LIFE issue fields and readiness shape | Jim (change by reviewed PR; Q04) | When writing, checking or validating a LIFE issue |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Canonical for numbered V7 decisions (D-numbers) | Dated product, runtime and architecture decisions | Jim (change by reviewed PR; Q04) | Only when a ticket cites a D-number: `python -m lifeos.sources.devtools.decisions show D<n>`; never read whole |
| [`docs/DECISIONS_INDEX.md`](docs/DECISIONS_INDEX.md) | Index of DECISIONS.md: no rule of its own | One line per decision | Jim (change by reviewed PR; Q04) | To find a decision number |
| [`docs/PRIVACY.md`](docs/PRIVACY.md) | Canonical hard rule for this public repository | What may never be committed, written in a PR or logged | Jim (change by reviewed PR; Q04) | Before any commit, PR text or log line |
| [`docs/LIMITS.md`](docs/LIMITS.md) | Canonical provider limits (code in lifeos/platform/limits.py) | Provider rate and volume ceilings | Jim (change by reviewed PR; Q04) | When a change calls an external provider |
| [`.github/pull_request_template.md`](.github/pull_request_template.md) | Format only | PR layout: Jira key, changes, evidence, not proven | Jim (change by reviewed PR; Q04) | When opening a PR |
| [`README.md`](README.md) | Pointer only | Repository one-paragraph summary and links | Jim (change by reviewed PR; Q04) | Optional |

## Keeping this index true

Adding, renaming or retiring a global source updates this index in the same PR. Other files under `docs/` are not listed: find them by listing the folder, each starts with its own heading (LIFE-644). `python -m lifeos.sources.devtools.readiness repo` and `tests/contracts/test_development_policy.py` fail when a listed path is missing, has no owner or authority, or is over the size limit.
