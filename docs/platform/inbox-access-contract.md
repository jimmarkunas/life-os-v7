# Action Inbox access contract: tool paths, grants and evidence (LIFE-707, Nov 14 2026)

Origin LOS-86 / REQ-7, under epic LIFE-696; builds on the LIFE-685 inventory in `action-inbox-readiness.md`. Read-only inspection of this repo and of the accepted edge documents, plus synthetic tests. No live provider, credential, secret, Oracle host or Notion page was touched, and nothing here claims off-host proxy support. Code: `lifeos/inbox/access.py` (one attributed, receipted read), `lifeos/inbox/ledger.py` (`v7_inbox_access`), tests: `tests/inbox/test_access.py`.

## A. One contract, no second gateway

A read of one source by one principal goes: existing grant check (`policy.read_grant`: TGP and unknown workspaces refused, then source, workspace, account, revoked, expired, action) -> receipt written to the existing V7 store -> read with only the grant's credential NAME. A refusal has a receipt and no read; a receipt that cannot be written means no read; a repeated request id never reads twice, also after a restart. Receipts hold ids and fixed words only. The `v7_inbox_access` table is code in `ensure_schema`, which nothing runs: creating it is part of the LIFE-712 schema proposal and needs Jim's approval.

## B. Tool paths by client (class: operational / synthetic / missing / retired)

| Client | Host | Identity the contract can see | Credential custodian | Permitted endpoint for the Inbox | Class |
|---|---|---|---|---|---|
| OpenClaw (the `main` Gateway agent) | Oracle edge VM, runtime user without root | agent name `main`, called by `openclaw agent` | Bitwarden through SecretRef; the agent gets placeholders, wrappers use the host credentials | Loopback Gateway only; allowlisted wrappers: Jira read, Calendar read, Todoist read, Notion read, GitHub read; a Todoist complete is refused | Operational Oracle-local for Jira, Calendar, Todoist reads (accepted Oct 13); missing for Asana, Concord, Followup |
| Claude Code on Oracle | same host | the session calls `openclaw agent`; no separate identity | none of its own: it holds no provider credential | the same allowlisted wrappers through the Gateway | Operational (consumer proof Oct 11, re-verified Oct 13) |
| Claude Code on the Mac | Mac | session id (a Concord agent id) | the user's own connected-app sign-ins; no V7 broker | none for the Inbox; no V7 read client uses it | Missing for the Inbox |
| Hermes | Oracle edge VM | Linux user and its stage workflow | the LIFE-306 host broker for its registered purposes | none; Hermes has no Inbox read path | Operational for its own roles; missing for the Inbox |
| Codex | not inspected | not defined in any V7 code or contract | not defined | none | Missing |
| Concord | Mac (Orca plugin), local MCP | per-session agent id | none held by Concord for providers | a read client for approvals and resume eligibility does not exist in V7 | Operational as a tool; missing as an Inbox reader (LIFE-710) |
| ChatGPT | off-host | native connected apps only | its own connector sign-ins | none: off-host use of the Gateway is not approved and not proven; generic proxying was removed on Oct 13 | Retired as a proxy route; missing for the Inbox |
| Composio | none in V7 | none | none | none | Synthetic only (four-route fixtures); no module |

Honest limits: the LIFE-306 broker keeps credential values out of prompts and logs, but the agent account on its host has passwordless root, and its caller name is forgeable by a root-capable caller; it was not run here. The administrator and runtime users on the edge VM are separate (verified Oct 16), so OpenClaw's user cannot reach host credentials.

## C. Per source and workspace: what real access would need (TGP excluded everywhere)

Owner of every row below is Jim: a named account, a scoped credential in the central secrets service, and a data scope, recorded before any real read. None exists today for the Inbox.

| Source | Personal | Bytalos | Reusable code today | STOP prerequisite |
|---|---|---|---|---|
| Jira | needs a named account and read scope | needs a named account and read scope | `lifeos/platform/jira.py` | Jim names the account per workspace; unverified whether a separate Bytalos identity exists |
| Calendar | needs a named Google account | needs a named Google account | `lifeos/platform/gcal.py` | same; a read-only scope, no write |
| Todoist | selected Personal tasks | not part of the contract | Oracle wrapper (read); no V7 client | Jim approves the account and the Personal-only scope |
| Asana | none planned | permitted programme work only | none | OAuth and scope approval; client or TGP projects excluded and ambiguous ones refused |
| Concord | approval and resume records | not scoped yet | none in V7 | decide where the durable read comes from and how workspaces are separated on the Mac |
| Followup OS | no live source | no live source | none | a verified Followup-owned projection must exist first; otherwise it shows unavailable |

## D. Evidence matrix (synthetic is not live)

| Claim | Evidence | Synthetic | Live |
|---|---|---|---|
| Two scoped agents read the same fake source; only the granted one reads | `test_the_other_agent_is_denied_before_any_read_and_still_gets_a_receipt` | Yes | No |
| Wrong workspace, wrong account, revoked, expired, wrong action, TGP, unknown scope deny with no read | `test_wrong_workspace_wrong_account_revoked_expired_and_wrong_action_all_deny_without_reading` | Yes | No |
| Receipts name the principal and survive a restart; a replay never reads again | `test_the_allowed_agent_reads_and_is_named_on_the_receipt`, `test_a_repeated_request_id_is_a_replay_and_never_reads_again_even_after_a_restart` | Yes | No |
| No receipt, no read; failures and receipts leak no text | `test_if_the_receipt_cannot_be_written_nothing_is_read`, `test_a_failed_read_is_recorded_as_its_type_only_and_hides_the_message`, `test_receipts_hold_ids_and_fixed_words_only` | Yes | No |
| Oracle-local OpenClaw wrappers answer Jira, Calendar and Todoist reads | `docs/EDGE_GATEWAY_SCOPE.md` consumer status (Oct 11 and 13) | n/a | Accepted, but not an Inbox grant |
| Any real Inbox grant, credential, or off-host path | none | n/a | Not proven |

## F. Jira and Calendar reads (LIFE-708): what exists and what Jim must approve

**Code (`lifeos/inbox/sources.py`, tests `tests/inbox/test_sources.py`).** `JiraSource` and `CalendarSource` plug into `inbox.build_inbox`. Each gets its data from ONE read function, never a client object: `Jira.board_issues` (GET) and `GoogleCalendar.list_events` (GET). A test records every call made through the real clients and sees only GET; another scans the module and finds no write name and no client construction. Records carry the canonical id, the source link, owner (Jira account id, or the calendar identity), the source's own status, a time zone and a revision that changes with the status; there is no description text and no shadow task store. A grant that is missing, revoked or expired, a TGP workspace, an erroring source, an incomplete listing and a malformed row each fail alone and show as health (`no_grant`, `refused`, `unavailable`, `degraded`) while the other sources keep their rows. The default reader refuses with `live_reads_not_authorized` and builds no client, so nothing can reach a provider today; `LIVE_READS_ENABLED` is False.

**Proof required before any live fetch (all recorded by Jim, in Jira).**

| Item | Jira | Calendar |
|---|---|---|
| Account, per workspace | Personal and Bytalos each named separately | Personal and Bytalos each named separately |
| Data scope | the board per workspace, and the query: Human UAT issues, plus open issues assigned to Jim; fields: title, status, assignee id, due date, updated | the calendars per workspace, a 14 day window; fields: title, start, status, link, updated |
| Credential | one scoped read token per workspace, held by name in the central secrets service | one credential per workspace with a READ-ONLY Google scope |
| Gap found | none for reads; the Jira client also holds write methods, which the source never receives | the existing credential scope is `calendar.events`, which also allows writes at the provider; a read-only scope (`calendar.events.readonly`) credential is needed so no write is possible even by mistake |
| Deployment | a read stage registered in the stage list and a grant record for the reading principal | same |

**Not yet allowed.** A live stage runs inside the hourly pipeline. Jim lifted the LIFE-686 freeze on November 21, 2026, so a live stage is released through its own `Agent Go` ticket. No Oracle work is needed for these two reads. Human UAT evidence is the issue's own link for now (its receipt comments live there); richer evidence such as linked pull requests belongs to the Human UAT workflow mapping in LIFE-714.

## G. Todoist and Asana reads (LIFE-709): what exists and what Jim must approve

**Code (`lifeos/inbox/sources.py`, tests `tests/inbox/test_sources_tasks.py`).** `TodoistSource` and `AsanaSource` follow the same pattern as section F: one injected read function each, no V7 client exists for either provider, nothing is fetched live, and the default reader refuses with `live_reads_not_authorized`. A provider that cannot be reached for missing OAuth or permission shows as `unavailable` with the fixed word `integration_blocked`, never as a fake success; the other sources keep their rows. Todoist is Personal only (a Bytalos Todoist source refuses with `todoist_personal_only`) and reads only the approved projects. Asana is Bytalos only (`asana_bytalos_only`) and reads only the approved programme projects: a task sitting in none of them is out of scope and left out; a task sitting in an approved project AND any other project, or in no project, is ambiguous, so it is rejected, counted and the source shows `degraded`; TGP and client projects never show. No Todoist completion and no Asana write exist in the module, and a test scans it for write and client names.

**Approvals Jim must record before any live fetch.**

| Item | Todoist | Asana |
|---|---|---|
| Account | the Personal account, named | the Bytalos programme account, named |
| Data scope | the approved project ids; fields: title, due, status, link, creation time | the approved programme project ids (a list that excludes every TGP and client project); fields: title, assignee, due, status, link, modified time |
| Credential | a scoped read token held by name in the central secrets service | a scoped OAuth or personal token, read only, held by name in the central secrets service |
| Gap found | no V7 client; Todoist's task listing has no update time, so a change to a task is not a new revision until the real shape is verified | no V7 client; the "which projects are Bytalos programme work" list must come from Jim, since a wrong list could show client work |
| Shapes | taken from the providers' public API documentation, not a live read | same |

**Not yet allowed.** Same as section F: a live stage in the hourly pipeline is released through its own `Agent Go` ticket (LIFE-686 freeze lifted November 21, 2026); no Oracle work is needed for these reads. Todoist completion is a separate later ticket (LIFE-717).
