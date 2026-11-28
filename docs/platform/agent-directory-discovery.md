# Agent Directory: V7 resolver, status classification and live-discovery gate (LIFE-736, Nov 16 2026)

Code: `lifeos/platform/agent_directory.py`, tests: `tests/platform/test_agent_directory.py`, fixture: `tests/platform/agent_directory_fixture.py` (the 24 rows read back from the Notion LIFE OS Agent Directory on 2026-11-16, read-only; public partial aliases only).

## A. What was built (synthetic, no live access)

A pure, read-only resolver over the Notion directory. The directory is a **catalog, not a capability token**: a row grants nothing and is never proof that an agent is online.

- `load(fetch, policy, principal, now)` checks the existing per-agent policy (`access_policy`, service `notion`, read) BEFORE calling `fetch`; the default reader refuses until the exact grant is approved (section B).
- `resolve(...)` returns ONE entry, by exact Agent ID or exact unique alias, for one workspace. It re-checks the grant on every call (a revoked grant denies at once). Wildcards, lists, `all`/`everyone` and two selectors are refused.
- `snapshot(...)` is the redacted machine-readable projection: fixed keys, sorted, provenance (`source`, `read_at`, `snapshot_at`, per-row `last_checked`), principal as an 8-character fingerprint, `authorizes: []`. Route text, notes and boundaries are not copied out.
- Fail closed with a fixed reason word: Notion unavailable, duplicate Agent ID, unknown record type or platform, missing or unknown workspace, a secret-looking value (nsec, private-key block, token), stale or future-dated rows, retired rows, partial identities, ambiguous aliases, TGP and Unknown workspaces. A full public npub is shortened to `npub…xyz`. Nothing is cached; no gateway, service, scheduler, database or polling was added.

Kinds: `agent_identity` (Buzz member, Hermes profile), `client_surface` (Claude/ChatGPT web, Mac, VS Code), `agent_session` (Orca/Concord, ephemeral), `persona` (Council Auditor/Challenger). A display name never merges two identities. One Hermes runtime on Oracle has six profiles (matches `hermes/connector-allowlists.txt`; checked by `check_hermes`); the Mac Hermes Desktop is a client, not a second server.

## B. Status classification (repo evidence only, as of 2026-11-16)

Words: `manual-only`, `configured-untested`, `not-reachable`, `routable-verified`. `routable-verified` needs BOTH a row marked Routable Verified/Verified AND a fresh `Proof` (live receipt, same host, within 1 hour) supplied by the caller; a row alone, a Candidate, a surface, a persona or a partial npub never qualifies.

| Group (rows) | Status | Why |
|---|---|---|
| Claude and ChatGPT surfaces (8): web, Mac app, Cowork, VS Code on Mac | manual-only | Human UIs; no verified programmatic route |
| Hermes profiles (4): main, claude-hermes, librarian, writer (developer merged into claude-hermes and research into librarian, Jim, Nov 27 2026; their two rows say Retired) | configured-untested | Repo config (`docs/hermes-files.md`, allowlists); the gateway serves these four and live memory_recall passed for main and librarian; no programmatic contact route |
| Orca/Concord adapters (2): claude-code, codex | configured-untested | `tools/orca-concord-room` README describes live `concord who`; not queried here |
| Buzz members (5 named + 1 unidentified) | not-reachable | Screenshot evidence, truncated npub tails only; membership and reachability unverified |
| Buzz personas (2): Council Auditor, Challenger | manual-only | A persona is not an addressable agent; member identity unresolved |

Routable today: **none**. Unavailable/manual: all 22 active rows (2 more are Retired).

## C. Scope item 4: live discovery is NOT done; exact read-only steps and approvals

Each step needs Jim's separate approval (named host, purpose, rollback = none, read-only) and none runs under LIFE-736 without it.

1. **Notion read grant (needed to wire a real `fetch`).** Approve the Notion read of the Agent Directory data source (its id comes from `lifeos.platform.notion_ids`, never committed here) for one named principal through the existing credential/grant wrapper; add that principal's `notion: read` row to the access policy.
2. **Mac / Orca / Concord (read-only):** `concord who` and `concord adapters status` in a local shell, saving only agent IDs, adapter names and session presence. Authorization: local Mac shell on Jim's machine.
3. **Oracle / Hermes (read-only):** `bash hermes/check_profiles.sh` on `opc@lifeos-edge-01` (prints names and hashes only), plus listing profile folder names under the Hermes home. Authorization: SSH to the Oracle host.
4. **Buzz (public keys only):** compare the community membership list of PUBLIC npubs with the directory's partial aliases through the existing scoped identity (`buzz_agent.py --as claudecode`, read). Never read or store an nsec. Authorization: Buzz read of the named channel/community.
5. **Web/desktop surfaces:** each provider's documented supported route (none is verified today); keep manual-only until one is.
6. A **proof** (`Proof(agent_id, host, session_id, verified_at, receipt)`) is produced only by a live check of one specific agent after steps 2 to 4, then Notion rows may be updated by Jim or an approved writer. A cross-agent send is a separate, approval-gated action.
