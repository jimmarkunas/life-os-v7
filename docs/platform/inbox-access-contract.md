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
