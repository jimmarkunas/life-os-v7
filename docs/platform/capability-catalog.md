# Capability catalog and agent identity contract (LIFE-810, F01, Wave 0)

**Scope:** source and synthetic only. No live provider or account access, no grant, no new credential, no host change. Extends the existing access layer; it is not a second service or gateway.
Reuses: `access_policy.py` (LIFE-374: allow, deny or pending), `agent_directory.py` (LIFE-736: a catalog, not a capability token), `inbox/access.py` (LIFE-707), `shared_read.py` (LIFE-721) and the LIFE-306 purpose-only broker.

## The contract

`lifeos/platform/capability_catalog.py` resolves `(principal, tool, action, workspace)` against `lifeos/platform/capability_catalog.json`:

| Returned on success | Meaning |
|---|---|
| `provider`, `account_alias`, `tenant` | which provider and which registered account alias; the tenant comes from the alias row |
| `host` | the host label the capability belongs to |
| `allowed_source`, `provenance` | the repo file (or doc) the capability is registered from, and its `as_of` date |
| `status`, `synthetic` | `working`, `synthetic` or `missing` (below) |

It never returns a credential or an endpoint, and the loader refuses a catalog that holds a credential-shaped key or value (`credential_in_catalog`). A row grants nothing: policy still decides allow, deny or pending.

Refusals use fixed codes and invent no route: `catalog_unreadable`, `unknown_principal`, `wrong_workspace`, `unknown_capability`, `unavailable` (status `missing`), `alias_missing`, `alias_duplicate`, `source_stale` (older than the row's `max_age_days`).

**Status meanings.** `working`: merged source with tests. `synthetic`: a contract proven by fixtures only. `missing`: not in source. None of them proves a live provider account; live access stays UNVERIFIED until a separately approved read-only check.

## Classification (Nov 20, 2026, from `origin/main`)

| Capability | Status | Source |
|---|---|---|
| Jira read, Notion write for a registered agent | working | `access_policy.py` |
| Buzz read | working | `lifeos/buzz/mcp.py` |
| Agent directory read | working | `agent_directory.py` |
| Gmail read for an agent | synthetic | `access_policy.py` (policy level only; no live account checked) |
| Doppler secret read | missing | none; evaluation only, below |

The catalog lists these six rows only. Other services in `access_policy.SERVICES` (GitHub, Drive, Calendar) are not registered yet; adding one is a data row plus a test, not new code.

## Machine-secrets custody matrix (evaluation only: NOT adopted, installed, connected, migrated or paid)

Doppler facts are from the vendor pages named in LIFE-810, read Nov 20, 2026; anything a page did not state is marked so. Bitwarden facts are what the live Secrets Manager shows today (Free plan).

| Question | Bitwarden Secrets Manager (in use) | Doppler Team (candidate) |
|---|---|---|
| Plan and cost | Free plan; 3 projects, 3 machine accounts, 120 secrets seen in the console | Team is $21 per user per month; the student page says free via GitHub Education (duration and eligibility not stated) |
| Structure | projects, secrets, machine accounts | 250 projects, 15 environments, 100 configs per environment; config inheritance on Team |
| Machine access | machine account token per project | service tokens (read-only to one config, optional write, optional expiry) and service accounts (identity not tied to a user, roles, 0..n tokens) |
| Raw values reach the consumer | yes, loaded into the job by the pinned action | yes: `doppler run` injects environment variables; tokens give config-wide read |
| Restrict to named secrets | by project | not stated; scope is per config or per role |
| Audit | not assessed here | activity log, 90 day retention |
| Rotation | manual | automatic rotation listed as a Team feature (limits not stated) |
| CI and CLI | pinned `bws-load` action | CLI (`run`, `secrets get`, `download`); API read endpoints not stated on the pages read |
| Failure case | job fails, one retry | revocation is immediate, but the CLI fallback file keeps the last values on disk |

Conclusion for Wave 0: keep Bitwarden for owner passwords, break-glass recovery and today's machine loading; keep the LIFE-306 purpose-only broker as the only agent-facing boundary; never give an agent a Doppler token, CLI or MCP. Whether Doppler is worth adopting is Jim's decision after a separate approval; this document provisions nothing and the student offer is unverified for this account.

## Consumer to broker map (design, nothing provisioned)

consumer (a registered principal) -> workspace (its `workspace`) -> config or project (provider-side, chosen per purpose) -> purpose (the capability's `tool` and `action`) -> broker call (the LIFE-306 broker resolves the purpose; the catalog only says which provider, alias and host apply). A vendor capability (what the plan could do) is never recorded as an active workplace; only a `working` or `synthetic` row with a real source is.

## Proof

Fake two-client resolver tests, refusal codes, credential refusal and the registry classification: `tests/platform/test_capability_catalog.py`.
