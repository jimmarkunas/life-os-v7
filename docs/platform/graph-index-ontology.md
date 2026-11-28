# Graph index: ontology and checkpoint contract (LIFE-1104, GI-1)

The AuraDB graph is a **derived, rebuildable relationship index**. Jira, Notion and GitHub stay canonical; Aura holds only ids, repo-scoped PR numbers, typed edges and an allowlisted status. If it is wrong or missing, a reader falls back to the canonical source. Code: `lifeos/platform/graph_index.py`; command: `tools/graph_index_sync.py`.

## What Aura may hold

| Node | Properties (nothing else) | Status values |
| --- | --- | --- |
| `Requirement` | `id` (`REQ-n`), `status`, `suppressed` | draft, approved, delivered, retired |
| `Ticket` | `key` (`LIFE-n`), `status`, `suppressed` | To Do, Agent Go, In Progress, Human UAT, Blocked, Done, Cancelled |
| `PullRequest` | `repo` (`owner/name`), `number`, `state`, `suppressed` | open, merged, closed |

Every node and edge also carries `ns`, the namespace of the load. Edges: `BLOCKS` and `CHILD_OF` (Ticket to Ticket), `IMPLEMENTS` (Ticket to Requirement), `DELIVERED_BY` (Ticket to PullRequest); property `suppressed` only. A title, text, Notion id, hash, URL or any other property fails validation before a write, as does a value shaped like a Notion id.

## Rules of a load

* **Namespaces:** only `gi1-fixture-v1` is allowed today (synthetic records). Any other namespace, or a namespace holding foreign content (another label, an unlisted property, an edge crossing out), stops with a non-zero exit **before any write**. Caps: 20,000 nodes and 40,000 relationships.
* **Additive only:** a load merges and sets; it never deletes. A record that leaves the source is **suppressed** (`suppressed = true`), and it returns by being written again. The live client has no delete or wipe; a wipe-and-rebuild exists on the in-memory client only.
* **Idempotent:** loading the same records twice gives the same graph and the same checksums. A live load into a namespace that holds anything other than the same records is refused.
* **Dry run first:** the command plans against an empty in-memory graph by default. A remote write needs `--apply` and `--live`, and runs on Oracle with the Bitwarden credentials by name.

## The checkpoint

Table `v7_graph_index_checkpoint`, in its **own** MySQL database (Bitwarden `lifeos/edge/CKPT_DB_HOST`, `_NAME`, `_USER`, `_PASSWORD`). It is not in the V7 store, and nothing in this code imports the V7 store connection or uses an SSH key or tunnel.

| Column | Meaning |
| --- | --- |
| `ns`, `generation` | primary key; one row per run, generation counts up from 1 |
| `status` | `building` when the run opens, `published` when it ends cleanly |
| `nodes`, `edges` | counts of everything stored, suppressed included |
| `checksum` | sha256 of every stored record (the graph as written) |
| `membership_digest` | sha256 of the active (not suppressed) records: the set a reader may serve |
| `created_at`, `published_at` | UTC |

Rules: a run inserts its row as `building`, writes, **reads the namespace back**, and only then marks the same row `published`; it never updates a published row and never deletes or replaces one. Two runs that pick the same generation collide on the primary key and the second stops; a `building` row younger than 15 minutes blocks a new run. A run that fails after it opened leaves its row `building`, so no half-built graph is ever served.

## What a reader may serve

Only the active records of the namespace, and only while the newest row is `published`, is at most **24 hours** old, and equals what is stored (checksum and membership digest). Otherwise the graph is **unserved**: `CHECKPOINT_MISSING`, `CHECKPOINT_BUILDING`, `CHECKPOINT_STALE` or `CHECKPOINT_MISMATCH`. A suppressed record, and an edge that touches one, is outside the membership and is never returned. There is no schedule: the bound only says how old a published generation may be.

## Not here

Real Jira, Notion or GitHub reads, remote deletion, client grants, schedules, and the gateway retrieval tools (GI-2, GI-3).
