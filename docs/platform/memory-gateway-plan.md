# Unified memory and the LIFE OS gateway: the plan to finish (LIFE-848; Jim, Nov 27 2026)

Plan only: each phase is built by its own released ticket, one PR at a time, in the order below. Everything here was
measured on main and on the live host on Nov 27 2026. Jim approved folding the first-class memory tools into LIFE-1078
and recording the gateway move as a follow-up under LIFE-848 (no new ticket now).

## Why

Honcho is the right memory backend and stays (one installation, private, its token held only by the gateway). The gap
is the gateway's shape: memory, Google, source lookup, the Agent Inbox and orchestration all run as operations of the
Buzz tools inside `lifeos/buzz/mcp.py`. That cost real failures on Nov 27:

| Client | What it sent | What happened | Patched by |
|---|---|---|---|
| Muse | memory fields flat beside `operation` | `unknown_tenant` (no workspace reached the router) | PR #1722 |
| a JSON-string client | `arguments` as a string | `unknown_tenant` | PR #1720 |
| Codex | a Buzz `community` beside nested memory arguments | `community_not_authorized` before the memory gate | PR #1731 |
| OpenClaw | `buzz_capabilities` called with a memory operation | `invalid_argument` (the model picked the wrong tool) | none (model choice) |
| Codex | discovery | `buzz_capabilities` lists `memory_store`, which Codex is not granted | none yet |

Each patch made the gateway more lenient about a wrong shape. The fix that removes the cause is to expose memory as its
own tools (phase 1) and, later, to move the gateway out of the Buzz package (phase 4).

## Baseline (Nov 27 2026)

| What | Value |
|---|---|
| Live release | single-commit, built from `deploy/buzz-mcp/release-manifest.txt` (38 lines) by `tools/buzz_release.py`, verified hash by hash |
| Package budget | `lifeos/buzz`: 765 non-comment lines against a 468 + 300 cap (`tests/buzz/test_bridge.py::Budget`); 3 lines left |
| Tool contract | `tests/buzz/test_contract.py`: exactly 8 tools (5 legacy `buzz_*`, 3 permanent `FROZEN`); `SCHEMA_FINGERPRINT` pins the 3 permanent schemas |
| Memory clients | chatgpt-chat, claude-chat, duet-chat, muse-chat, mac-claude-code, oracle-claude-code, openclaw, hermes-librarian (all five memory tools); jira-capture (store, correct, revoke); codex (recall and discovery, until 2027-02-25) |
| Production recall | `lifeos/platform/memory_honcho.py`: default 5, max 20; tombstone `listing()` stops at 2,000 rows without an error; no grant re-check after the gate |
| Personal memory | 36 rows: 15 facts, 21 tombstones; fact sources: 6 Jira keys, 1 URL, 8 chat or session free text |
| Alerts | event-driven in the service (`lifeos/platform/memory_monitor.py`); no timer |

## Rules for every phase (deterministic development)

1. **One ticket, one PR, one release.** The ticket is released to Agent Go by Jim; the executor claims it, posts a
   dry run, builds on its own worktree from fresh main, merges on same-head green CI, releases, proves, then closes.
2. **Release only from one merged commit.** `tools/buzz_release.py build <commit>`, stage a new immutable
   `/opt/lifeos-buzz-mcp/releases/<sha12>`, `verify` must report 0 differences, dry-load as `buzz-mcp`, then switch
   `current` and restart. Before the switch, confirm `current` still names the release you last saw (another agent may
   have released). Rollback is always `ln -sfn releases/<previous> current` and a restart.
3. **The manifest follows the code.** A new module the service imports must appear in the manifest
   (`tests/buzz/test_buzz_release.py` fails otherwise); rebuild it with `tools/buzz_release.py closure`.
4. **Contracts change only on purpose.** A phase that changes the tool list updates `test_contract.py` in the same PR
   and says so in the PR body. The 3 permanent schemas and `SCHEMA_FINGERPRINT` never change.
5. **No grant, credential or identity change** unless the ticket names it. Grants live in
   `lifeos/platform/mcp_access.json` and `capability_catalog.json`; credentials are hashes only.
6. **Privacy.** No Personal memory text in Jira, GitHub, logs or transcripts. Proofs report status, counts, fixed
   codes and fingerprints only. Read Personal only as counts, inside `BEGIN READ ONLY`.
7. **Proof is the smoke matrix below, run after every release that touches the gateway.** A phase is not done until
   every client in its scope passes, or is reported UNVERIFIED with the reason.
8. **Times** in Jira and receipts come from `python3 -m lifeos.platform.ct_stamp`, never typed.

### Smoke matrix (run after each gateway release)

Prompt for a chat or agent client: *"Run the LIFE-1081 memory smoke test: recall from workspace Personal, query
'LIFE-1081 smoke probe'. Reply with only: success status, result count, and whether answer_rule is present. Do not
show memory text, store anything or call memory_ask."* A pass is success, a count (0 is fine) and `answer_rule` present.
Confirm each from the server's receipts (`journalctl -u lifeos-buzz-mcp`, the client's `principal_fp`, tool
`memory_recall`, outcome `ok`).

| Client | Who runs it | How |
|---|---|---|
| ChatGPT, Claude.ai, Duet, Muse | Jim | one prompt in each app |
| OpenClaw | Jim (Telegram or WhatsApp) or OC (`openclaw agent --agent main` on Oracle, no `--deliver`) | prompt above |
| Codex | Jim, or OC through `~/.lifeos/bin/lifeos_mcp_stdio.py --credential codex` | prompt above |
| Mac and Oracle Claude Code | OC | authenticated call with the Keychain credential, codes only |
| Hermes librarian | OC or Jim | prompt above in a new session |
| Negative checks | OC | a Bytalos recall is refused `catalog_unknown_*`; Codex `memory_store` is refused; an unauthenticated POST is 401 |

## Phase 1: LIFE-1078, recall safeguards and first-class memory tools

**Blocked by:** Jim's provenance decision (B2 below) and his acceptance of the synthetic response preview.

**A. Recall safeguards** (`lifeos/platform/memory_honcho.py`)
- Default and hard cap 3 (history rows included); a smaller valid `top_k` is honored; anything else is
  `invalid_argument`.
- Each candidate is re-read with `Honcho.get(workspace, id)` before output; a changed envelope is dropped and counted.
- `listing()` fails closed: a page error or more than 20 pages raises `memory_unavailable` (no partial tombstone list).
- Grant re-check before output (ChatGPT ruling B1, LIFE-848#14712): run the full existing gate again
  (`mcp_access.load()` plus the same `authorization_gate.execute` decision) after the Honcho read and before the
  reply, without re-running the effect or widening any grant. A denial at that point returns the fixed refusal.
- Provenance: per Jim's B2 decision. Either legacy chat or session sources are excluded and counted
  (8 to 9 of 15 current facts leave recall), or a "verified by the storing client" class keeps them, labelled.
- Response shape unchanged plus counts of excluded rows by fixed reason (never their text).

**B. First-class memory tools** (folded in by Jim, Nov 27 2026)
- Add `memory_recall`, `memory_store`, `memory_correct`, `memory_revoke` and `memory_ask` as top-level MCP tools, with
  explicit `workspace` (enum `Personal`) and the operation's own parameters. No `community` and no nested `arguments`.
  Descriptions come from `memory_honcho.OPS`, so the answer rule travels with them.
- `tools/list` shows only the memory tools the client holds a live grant for (Codex sees `memory_recall` only;
  jira-capture sees store, correct and revoke).
- Each tool goes through the same `governed()` path and gate as today; receipts keep the same `tool` names.
- The `buzz_read` and `buzz_publish` memory operations stay as compatibility aliases (the leniency from PRs #1720,
  #1722 and #1731 included) until phase 3 retires them.
- `buzz_capabilities` lists only the memory operations the caller is granted.
- Size: the tool table and registration live in `lifeos/platform/` (for example `memory_tools.py`), with at most 3
  added lines in `lifeos/buzz/mcp.py`; if that cannot fit the budget, STOP and report (phase 4 is the fix, not a
  budget raise).
- Contract: `test_contract.py` changes from "exactly 8 tools" to 8 plus the granted memory tools, in the same PR; the
  3 permanent schemas and `SCHEMA_FINGERPRINT` are untouched. Each new tool's schema is pinned by its own new test.

**Tests:** the LIFE-1064 adversarial and mutation cases ported to production fakes (`tests/platform/test_memory_honcho.py`);
listing overflow; changed revision; concurrent revoke; output-time grant revocation; per-client `tools/list` (codex,
jira-capture, a five-tool client); the alias path still works; Bytalos and TGP memory still refused.

**Done when:** merged, released by rule 2, the smoke matrix passes for every granted client with the new tools and
through the alias, and the negative checks hold.

## Phase 2: LIFE-1079, the answer rule on real memory

**Blocked by:** phase 1 Done, and Jim's privacy route (C1). Every active client keeps a transcript today, so under the
no-retention rule the honest outcome is UNVERIFIED for all of them unless Jim names a route (a client with history off,
checked by him) or accepts UNVERIFIED with the synthetic and proxy evidence (LIFE-1064#14664).

**If a route exists:** Jim privately freezes 60 questions (20 answerable, 20 hard negatives, 20 absent) and labels them;
only counts are ever shared. Bars: exact at least 90% on answerable, correct silence at least 95% on hard negatives and
on absent, zero fabricated answers. A client below a bar gets a named fix (instruction, tool description or server
gate); no production edit inside this phase.

## Phase 3: LIFE-1081 close-out, every client on the first-class tools

- Move every client's standing instruction to name the first-class `memory_recall` tool (AGENTS.md line 37, the six
  Hermes `SOUL.md` roles, OpenClaw `workspace/AGENTS.md`, and Jim's chat settings for Claude.ai, ChatGPT, Duet and Muse).
- OpenClaw: replace `lifeos-buzz__buzz_read` in its two allowlists (`agents.entries.main.tools.allow` and
  `tools.byProvider["openai/gpt-6-luna"].allow`) with `lifeos-buzz__memory_recall` (and keep
  `lifeos-buzz__buzz_capabilities` only if still needed).
- Run the smoke matrix on the first-class tool for every client. Then retire the `buzz_read`/`buzz_publish` memory
  aliases in one PR, if and only if 7 days of receipts show no alias use (count from the journal, by tool name).
- Decide, with Jim, whether OpenClaw keeps store (it used store 3 times in the week to Nov 27).

## Phase 4 (follow-up under LIFE-848): move the gateway out of the Buzz package

Not a new server, unit, URL, port, credential or store.
- Move the gateway (identity, `governed()`, routing, tool registries, capabilities) from `lifeos/buzz/mcp.py` to
  `lifeos/platform/gateway/` (for example `server.py`, `tools.py`, `routes.py`). `lifeos/buzz/` keeps only the Buzz
  domain: relay, NIP-98 signing, message tools.
- Keep `lifeos/buzz/mcp.py` as a thin entry point (`python -m lifeos.buzz.mcp` still starts the same server), so
  `lifeos-buzz-mcp.service`, its `ExecStart`, the tailnet and Funnel URLs and every client's connector stay unchanged.
- Move the size budget with the code: `lifeos/buzz` gets a smaller cap; the gateway gets its own, measured at the move.
- Rebuild the manifest (rule 3); the release is still one commit (rule 2).
- **Done when:** the smoke matrix passes for every client with no client reconfigured, and `test_contract.py` passes
  unchanged from phase 3.

## Owner decisions still open

| Id | Decision | Needed by |
|---|---|---|
| B2 | Provenance: exclude legacy chat or session sources (8 to 9 of 15 current facts leave recall), or a "verified by the storing client" class | phase 1 |
| — | Accept the synthetic recall response preview before deploy | phase 1 |
| C1 | Privacy route for real-memory validation, or accept UNVERIFIED | phase 2 |
| — | OpenClaw: keep or remove store | phase 3 |
| — | Release phase 4 as a ticket | after phase 3 |

## Not in scope

Conversation learning (LIFE-1080 is DEFER until its prerequisites (a) to (f) are met), a second gateway, direct agent
connections to Honcho, the Neo4j graph in production, and any change to Buzz messaging behavior.
