# Action Inbox: shared-platform readiness, synthetic contract and Notion verdict (LIFE-685, Nov 14 2026)

Origin LOS-86 / REQ-7. Everything below is synthetic or read-only inspection; no live provider, credential or Oracle change was used. Code: `lifeos/inbox/`, tests: `tests/inbox/`.

## A. What exists (read-only inventory)

| Component | Where | Class | Reuse / gap |
|---|---|---|---|
| Per-agent service policy (read/write/delete/bulk, fail closed, audit line) | `lifeos/platform/access_policy.py`, `tests/platform/test_access_policy.py` (LIFE-403 / LIFE-374) | Implemented and verified (unit tests); synthetic only, no live caller | Pattern reused. Keys agents by 64-hex Buzz key and six services (jira, notion, github, gmail, drive, calendar); no Todoist, Asana, Followup or Concord service, no workspace or account dimension |
| Host secrets broker | `lifeos/edge/secrets/lifeos_secret_use.py` (LIFE-306) | Implemented but unverified in this review (root-only host program, not run) | Credentials never leave it; the Inbox stores only a credential NAME. Honest limit in its own header: agent account has passwordless root |
| Bitwarden boundary | `docs/SETUP.md`, central secrets service | Scoped only | No grant was read or changed |
| Composio / Google four-route fixtures | Jira LIFE-379/380/381 (Done) | Synthetic only; no Composio module in V7 (only a consent test) | Missing in code |
| Oracle-local OpenClaw capabilities | `docs/EDGE_GATEWAY_SCOPE.md` | Scoped only (Oracle-local; off-host consumers not accepted) | No Inbox use |
| Concord | external MCP (`mcp__concord__*`), `tools/orca-concord-room`, `lifeos/platform/agent_files.py` (instruction block only) | Implemented (Mac/Orca LIVE per module inventory); no V7 Python read client | Missing: a Concord read adapter |
| Buzz relay / MCP | `lifeos/buzz/` | Implemented and verified (LIVE) | Optional correlation ids only |
| Source registry / stage infra | `lifeos/platform/source_registry.json`, `lifeos/run.py` | Implemented and verified | Not needed by the contract; the live read stage will register here |
| Jira, Google Calendar read clients | `lifeos/platform/jira.py`, `gcal.py` | Implemented and verified | Reusable for the real Jira/Calendar reads |
| Todoist, Asana, Followup OS clients | none (only mentions in docs) | Missing | Needed for real reads |
| Exact-authorization policy | none in code (policy text only) | Missing | `lifeos/inbox/policy.py` adds a small single-use, bound, signed authorization; needs Jim's review before any live write |
| Retired | LIFE-401/403 "universal layer", LIFE-369 Epic | Retired / Cancelled | Not reopened |

Security concerns: (1) `access_policy` has no tenant dimension, so Personal/Bytalos isolation lives only in the new grant model; (2) the broker cannot stop a root-capable agent; (3) no live proof of any grant lifecycle.

## B. The contract (what the code does)

- `model.py` one `Record`: source-qualified id `source:workspace:account:native_id`, source URL, account, workspace, title, description, assignee, source status, presentation (needs_me / waiting / approvals / upcoming), tz-aware due, source and retrieval times, health, approval flag and evidence references, correlation id, revision. TGP and unknown workspaces are refused.
- `adapters.py` six normalizers (native dict in, record out). `inbox.py` builds one combined list; each (source, workspace, account) is read with only its own credential name, fails alone, and TGP never reaches a fetch. `view()` is All / Personal / Bytalos.
- `policy.py` the approved matrix, grants that can only narrow it, record eligibility, and the exact authorization. `actions.py` runs an action once, claims completion only from the source read-back.
- `notion_binding.py` the Notion event binding below.

## C. Notion feasibility: PARTIAL

Works natively (Notion help "Webhook actions"; developers.notion.com webhooks): a database view with workspace label and filters, a "Requested action" property, links, freshness text written by Oracle, and signed integration webhooks (`X-Notion-Signature` HMAC-SHA256, `authors[].id` of type person, `workspace_id`, `entity.id`, retries up to 8 times).

Limits: (1) the button "Send webhook" needs no authentication, has no documented signature, its payload is not previewable, button properties cannot be selected into it, and it is POST-only and paid-plan-only, so it cannot identify who pressed it, and a custom-header secret would be a reusable token in a page: not used. (2) A property edit is one click: protected actions use a second step (a challenge code Oracle writes beside the row, typed back by the same person). (3) Events are at-most-once and sparse; Oracle reads the row fresh. (4) Unverified live: delivery latency, coalescing of rapid edits, and whether a property-update event is raised for a select change on a database row (needs one test page and a webhook subscription: a protected step).

Binding proven synthetically (`tests/inbox/test_notion_binding.py`): the person, workspace and row come only from the verified signed event; the record and action come from the server-side row map and a fresh read; forged, edited, replayed, bot-authored, non-allowlisted, wrong-workspace and tampered-row events are refused.

## D. Next finite tickets (none exist yet; LIFE-369 stays Cancelled; no delivery parent exists)

1. Six-source read-only adapters on Oracle (Jira and Calendar from the existing clients; Todoist, Asana, Followup OS, Concord new), behind the LIFE-306 broker, one workspace pair at a time. Needs Jim: credential and Bitwarden grants.
2. Notion Action Inbox view: Oracle writes the combined read-only rows with workspace labels, filters and freshness through the existing Notion writer (one writer per surface).
3. Notion action-binding live proof on one test page (subscription, event shape, latency). Needs Jim: Notion integration and an Oracle endpoint.
4. Approved actions live, one source at a time (Jira Human UAT first), each with read-back; revisit the read-back status expectations in `policy.EXPECTED_STATUS` against the real workflows.
