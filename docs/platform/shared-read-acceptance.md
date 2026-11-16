# Shared read acceptance: two clients, one Jira source (LIFE-721, Nov 16 2026)

Chosen by Jim, Nov 16 2026: clients **Claude Code** and **Codex** (both on the Mac), source **Jira LIFE ticket reads** (read-only, non-sensitive). Code: `lifeos/platform/shared_read.py`, tests: `tests/platform/test_shared_read.py`.

**Classification: SYNTHETIC ONLY.** Nothing here has read Jira, a credential, the Mac or Oracle. LIFE-721 is not accepted until section C runs live and its receipts are read back.

## A. What the code proves (synthetic)

One call, `shared_read.read`, for the same Jira read from two distinct identities. It reuses `access_policy.decide` (Jira is read-only in v1) and adds no gateway, service, store, scheduler or cache.

- Order: validate, decide, write the receipt, then read. No receipt written, no read.
- The caller holds no credential. A grant names a credential NAME; only the injected host-side `fetch` uses it. Output keeps four fields (key, summary, status, updated); a secret-shaped value in the answer is refused.
- Receipt per call: time (Central), client, principal fingerprint (8 hex of a hash), tenant, tool, request id, source request id, outcome, fixed reason, response age. No arguments, no issue text.
- Refused with a fixed word and a receipt: `unknown_tool`, `write_refused`, `tenant_excluded` (TGP), `unknown_tenant`, `untrusted_session`, `policy_denied`, `no_grant` (wrong tenant, wrong account, borrowed principal), `grant_revoked`, `grant_expired`. A stale (over 300 s) or future-dated response, a connector failure and a secret in the answer are returned as `stale_response`, `fetch_failed`, `output_has_secret` with no data.
- Personal and Bytalos share no grant, identity or cache. Revoking one client leaves the other client, other services and other sources unchanged.

## B. What is NOT proven

- No real principal, session authentication or `trusted` flag exists yet; the tests set it by hand.
- The existing broker (`lifeos-secret-use`, LIFE-306) lives on `opc@lifeos-edge-01`, and its own header says the agent account there has passwordless root. Both clients run on the Mac, and `docs/EDGE_GATEWAY_SCOPE.md` does not accept off-host consumers. So a Mac-to-Oracle read path is a decision for Jim (C1), not something this code assumes.
- Codex is `action_required` (its managed app-server daemon is not ready; read from `concord adapters status` on Nov 16 2026).
- No revocation has been run against a real grant.

## C. Live steps and the separate approvals each needs

Each step is read-only and needs Jim's exact written approval (principal, host, purpose, rollback). None runs under the code PR.

1. **Decide the host path (Jim).** Choose one: (a) the proof runs on Oracle, with each client calling as its own Unix user through the broker; (b) a Mac-side read path through a new, approved credential store. (a) needs a broker purpose per client and an SSH approval; (b) adds a credential location and is a larger change. Recommendation: (a), because the broker, its audit log and its purpose registry already exist.
2. **Jira read grant per client principal (Jim).** One read-only Jira credential name per client, registered as its own broker purpose (`/etc/lifeos-secrets/purposes.d/`, action `http_get_fields`, fields limited to the four above), callers limited to that client's user. Tenant and Jira account named exactly. Rollback: delete the purpose file; the grant also gets an expiry.
3. **Make Codex usable (Jim runs it on the Mac).** Read-only check first: `concord adapters doctor`. Then launch Codex through `concord adapters codex` so its local app-server starts, and confirm `concord adapters status` no longer says `action_required` for codex. This installs nothing new; if `doctor` says an install is needed, stop and ask.
4. **Run the proof (Claude Code, after 1 to 3).** Both clients read the same LIFE issue; keep both receipts and the broker log lines; check separate fingerprints and separate source request ids.
5. **Run the denials live (Claude Code).** Wrong tenant, unknown tool, a write attempt, an expired grant. Each must produce a refusal receipt and no Jira request.
6. **Revoke one client (Jim approves, Claude Code runs).** Remove one client's purpose; confirm that client is refused, the other still reads, and no other source changed. Then restore or leave it removed as Jim decides.
7. **Write the receipt.** Endpoints, permitted tool, source request ids, commands run, read-back evidence and any residual gaps; every route not exercised stays classified synthetic only. No claim of universal client or tool coverage.

## D. Approvals Jim still has to give

1. The host path in C1.
2. The Jira read credential and broker purpose per client in C2 (new credential names and who may call them).
3. Permission to run the Codex step in C3 (or to have Claude Code run it).
4. The SSH or Mac access for steps 4 to 6, named by host.
5. The revoke test in C6, including whether to restore afterwards.

Test commands (local, synthetic): `python3 -m unittest tests.platform.test_shared_read`.
