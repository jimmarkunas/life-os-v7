# LIFE OS global source index

Routing manifest for the global sources in this repository (LOS-74 decision Q11; LIFE-222). It says which file answers which question, what authority it has, who owns it and how to retrieve it. It contains no rules: every rule stays in the source it names, and the [Development Policy](docs/life-os-development-policy.md) decides precedence. GitHub `main` is canonical; record the commit SHA you read at task start (Q06).

Project-specific context (requirements, workflows, configuration) is not here. It lives in the ChatGPT Project's Drive `PROJECT-INDEX.md` (LIFE-223); execution and status live in Jira `LIFE`, product decisions in Jira `LOS` (Q16).

## Global sources

| Path | Authority | Purpose | Owner | Retrieval |
|---|---|---|---|---|
| [`INDEX.md`](INDEX.md) | Routing only: no rule of its own | This manifest: which global source answers which question, who owns it and how to fetch it | Jim (change by reviewed PR; Q04) | Read first in a fresh session, then fetch only the rows the task needs |
| [`AGENTS.md`](AGENTS.md) | Bootstrap pointer, subordinate to the Development Policy | Agent start-up: Fast Path, repository routing, budgets | Jim (change by reviewed PR; Q04) | Read on every coding task |
| [`CLAUDE.md`](CLAUDE.md) | Bootstrap pointer, subordinate to the Development Policy | Claude Code entrypoint: start order, time zone, Deadline Mode trigger | Jim (change by reviewed PR; Q04) | Read on every Claude Code task |
| [`docs/life-os-development-policy.md`](docs/life-os-development-policy.md) | Canonical: the Development Policy entrypoint and index | Global engineering, delivery and agent-execution policy; links every section to its part | Jim (change by reviewed PR; Q04) | Read the index, then only the parts the task needs |
| [`docs/development-policy/01-status-mission-ui-first.md`](docs/development-policy/01-status-mission-ui-first.md) | Canonical: binding policy text (same authority as the whole policy) | Status, mission and UI/UX-first delivery | Jim (change by reviewed PR; Q04) | Fetch when the task or a section reference names it |
| [`docs/development-policy/02-jira-planning-model.md`](docs/development-policy/02-jira-planning-model.md) | Canonical: binding policy text (same authority as the whole policy) | Jira planning authority, feature-Epic model, components and versioning | Jim (change by reviewed PR; Q04) | Fetch when the task or a section reference names it |
| [`docs/development-policy/03-jira-issue-writing-contract.md`](docs/development-policy/03-jira-issue-writing-contract.md) | Canonical: binding policy text (same authority as the whole policy) | LIFE Jira issue-writing contract | Jim (change by reviewed PR; Q04) | Fetch when the task or a section reference names it |
| [`docs/development-policy/04-work-selection-lifecycle-reconciliation.md`](docs/development-policy/04-work-selection-lifecycle-reconciliation.md) | Canonical: binding policy text (same authority as the whole policy) | Deterministic work selection, Jira-first lifecycle, requirement-change reconciliation | Jim (change by reviewed PR; Q04) | Fetch when the task or a section reference names it |
| [`docs/development-policy/05-discovery-context-efficiency.md`](docs/development-policy/05-discovery-context-efficiency.md) | Canonical: binding policy text (same authority as the whole policy) | LOS-first discovery and context efficiency | Jim (change by reviewed PR; Q04) | Fetch when the task or a section reference names it |
| [`docs/development-policy/06-locked-architecture.md`](docs/development-policy/06-locked-architecture.md) | Canonical: binding policy text (same authority as the whole policy) | Locked architecture and execution constitution | Jim (change by reviewed PR; Q04) | Fetch when the task or a section reference names it |
| [`docs/development-policy/07-bootstrap-bounded-autonomy.md`](docs/development-policy/07-bootstrap-bounded-autonomy.md) | Canonical: binding policy text (same authority as the whole policy) | Bootstrap, Claude Code delivery mode and bounded autonomy | Jim (change by reviewed PR; Q04) | Fetch when the task or a section reference names it |
| [`docs/development-policy/08-deadline-delivery-gates.md`](docs/development-policy/08-deadline-delivery-gates.md) | Canonical: binding policy text (same authority as the whole policy) | Deadline Mode, delivery and activation contract, outcome and blocker gates | Jim (change by reviewed PR; Q04) | Fetch when the task or a section reference names it |
| [`docs/development-policy/09-fast-lanes-workspace-runtime.md`](docs/development-policy/09-fast-lanes-workspace-runtime.md) | Canonical: binding policy text (same authority as the whole policy) | Fast shipping, fast-lane coordination, workspace affinity, runtime completeness | Jim (change by reviewed PR; Q04) | Fetch when the task or a section reference names it |
| [`docs/development-policy/10-mutation-proof-tests.md`](docs/development-policy/10-mutation-proof-tests.md) | Canonical: binding policy text (same authority as the whole policy) | Mutation, proof, tests and TestKit | Jim (change by reviewed PR; Q04) | Fetch when the task or a section reference names it |
| [`docs/development-policy/11-review-coordination-completion-budgets.md`](docs/development-policy/11-review-coordination-completion-budgets.md) | Canonical: binding policy text (same authority as the whole policy) | Review, coordination, completion, budgets and metadata | Jim (change by reviewed PR; Q04) | Fetch when the task or a section reference names it |
| [`docs/development-policy/manifest.json`](docs/development-policy/manifest.json) | Verification record: no rule of its own | Part order, baseline hash and amendments; proves the parts are the whole policy | Jim (change by reviewed PR; Q04) | Only when editing the policy (update it in the same change) |
| [`docs/jira/issue-schema.v1.json`](docs/jira/issue-schema.v1.json) | Derived: machine-readable form of the issue-writing contract; the policy part wins | LIFE issue fields and readiness shape | Jim (change by reviewed PR; Q04) | When writing, checking or validating a LIFE issue |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Canonical for numbered V7 decisions (D-numbers) | Dated product, runtime and architecture decisions | Jim (change by reviewed PR; Q04) | Never whole: find the number in DECISIONS_INDEX.md, then grep `^## D<n> ` |
| [`docs/DECISIONS_INDEX.md`](docs/DECISIONS_INDEX.md) | Index of DECISIONS.md: no rule of its own | One line per decision | Jim (change by reviewed PR; Q04) | To find a decision number |
| [`docs/PRIVACY.md`](docs/PRIVACY.md) | Canonical hard rule for this public repository | What may never be committed, written in a PR or logged | Jim (change by reviewed PR; Q04) | Before any commit, PR text or log line |
| [`docs/LIMITS.md`](docs/LIMITS.md) | Canonical provider limits (code in lifeos/platform/limits.py) | Provider rate and volume ceilings | Jim (change by reviewed PR; Q04) | When a change calls an external provider |
| [`docs/CODEMAP.md`](docs/CODEMAP.md) | Generated routing map: no rule of its own | Stages, packages and where code lives; twin of docs/codemap.json | Generated by `python -m lifeos.codemap` | To locate code; regenerate, never hand-edit |
| [`docs/codemap.json`](docs/codemap.json) | Generated routing map: no rule of its own | Machine-readable twin of CODEMAP.md | Generated by `python -m lifeos.codemap` | Tools only |
| [`.github/pull_request_template.md`](.github/pull_request_template.md) | Format only | PR layout: Jira key, changes, evidence, not proven | Jim (change by reviewed PR; Q04) | When opening a PR |
| [`README.md`](README.md) | Pointer only | Repository one-paragraph summary and links | Jim (change by reviewed PR; Q04) | Optional |

## Not global sources

These repository files hold feature scoping, setup, history or one product area's context. They are not global policy: fetch one only when the LIFE issue or a global source names it, and never treat it as overriding the sources above.

| Path |
|---|
| [`docs/BIG_FEATURES_ROADMAP.md`](docs/BIG_FEATURES_ROADMAP.md) |
| [`docs/DCC_CARD_CONTRACT.md`](docs/DCC_CARD_CONTRACT.md) |
| [`docs/DELIVERY_TRACKING_SCOPE.md`](docs/DELIVERY_TRACKING_SCOPE.md) |
| [`docs/DRIVE_RESUMES.md`](docs/DRIVE_RESUMES.md) |
| [`docs/EDGE_GATEWAY_SCOPE.md`](docs/EDGE_GATEWAY_SCOPE.md) |
| [`docs/FIT_MODEL.md`](docs/FIT_MODEL.md) |
| [`docs/INTERVIEW_HANDOFF.md`](docs/INTERVIEW_HANDOFF.md) |
| [`docs/LANES.md`](docs/LANES.md) |
| [`docs/MASTER_ROADMAP.md`](docs/MASTER_ROADMAP.md) |
| [`docs/MEGIBOW_SCOPE.md`](docs/MEGIBOW_SCOPE.md) |
| [`docs/NETWORK_INTELLIGENCE_SCOPE.md`](docs/NETWORK_INTELLIGENCE_SCOPE.md) |
| [`docs/NOTION_INTEGRATIONS.md`](docs/NOTION_INTEGRATIONS.md) |
| [`docs/PLAN.md`](docs/PLAN.md) |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) |
| [`docs/SCHEDULER_AUTHORITY_DELTA.md`](docs/SCHEDULER_AUTHORITY_DELTA.md) |
| [`docs/SETUP.md`](docs/SETUP.md) |
| [`docs/US_REMOTE_WEB.md`](docs/US_REMOTE_WEB.md) |
| [`docs/buzz-implementation-roadmap-v1.md`](docs/buzz-implementation-roadmap-v1.md) |
| [`docs/buzz-remote-mcp-deploy.md`](docs/buzz-remote-mcp-deploy.md) |
| [`docs/contracts/career-lifecycle.requirements.v1.json`](docs/contracts/career-lifecycle.requirements.v1.json) |
| [`docs/prototypes/hiring-decision-center.html`](docs/prototypes/hiring-decision-center.html) |

## Keeping this index true

Adding, renaming or retiring a global source, or any file directly under `docs/`, updates this index in the same PR. `tests/contracts/test_development_policy_split.py` fails when a listed path is missing or a file under `docs/` is not classified here.
