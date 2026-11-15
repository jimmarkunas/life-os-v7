# Action Inbox schema rollout proposal (LIFE-712, Nov 15 2026)

Under LIFE-696; builds on LIFE-697 (`lifeos/inbox/ledger.py`) and LIFE-707 (the access receipts table). This is a **proposal**. Nothing creates a table until Jim approves the exact environment, database and the DDL below in a typed answer on LIFE-712. Code: `lifeos/inbox/schema.py` (`plan()`, `check()`), stage `inbox-schema-check` (read-only), tests `tests/inbox/test_schema.py`.

## 1. What exists, from the code (no live system was touched to write this)

* The V7 store is one MySQL-compatible database reached by `lifeos.platform.db.connect()`: an SSH tunnel to the database server's loopback port 3306, then a short MySQL session with `autocommit=True` and charset `utf8mb4`. Its settings are the existing secrets `LIFEOS_ACQ_DB_NAME`, `_DB_USER`, `_DB_PASSWORD` and the `LIFEOS_ACQ_SSH_*` set. The ledger uses that same function, so no new service, host, credential or datastore is involved.
* Which provider hosts the server, its version, its default row format and the account's exact privileges are **not stated anywhere in the repo**. The read-only stage `inbox-schema-check` reports them as flags (section 6). Do not assume Hostinger.
* Other V7 tables are created the same way, by an `ensure_schema` run from a stage with the same account; the inbox tables have never been created (`ensure_schema` is not called by anything).

## 2. The exact DDL (generated from `ledger.SCHEMA`, a test keeps them equal)

Four tables, all `CREATE TABLE IF NOT EXISTS`, `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`. They hold ids, fixed state words, hashes and UTC time strings only: never a title, description, credential, challenge code or error text.

| Table | Key columns | Purpose |
|---|---|---|
| `v7_inbox_events` | PK `event_id` | a delivery id is handled once, ever |
| `v7_inbox_intents` | PK `intent_id`; unique `event_id`, `nonce`; index `(row_id, state)` | a pending, confirmed or expired confirmation |
| `v7_inbox_actions` | PK `request_id`; unique `nonce`; unique `(record_id, action, revision, slot)` | one claimed action per request, nonce and revision |
| `v7_inbox_access` | PK `request_id` | an attributed receipt for every read of a source |

The statements are the four strings in `lifeos/inbox/ledger.py` `SCHEMA`; print them with `python -c "from lifeos.inbox import schema; print(*schema.plan(), sep=';\n')"`.

**One real risk found:** `uq_inbox_action_slot` covers `record_id VARCHAR(400)`, `action`, `revision` and `slot` at 4 bytes per character, about 2,096 bytes. InnoDB accepts that only with DYNAMIC (or COMPRESSED) rows (limit 3,072 bytes); on a server whose default row format is COMPACT the limit is 767 bytes and `CREATE TABLE` fails for `v7_inbox_actions`. The check reports the default row format; if it is not `dynamic`, the proposal changes to a shorter `record_id` prefix key before anything is applied.

## 3. Privileges the account needs

`CREATE` (once, for the four tables) and `SELECT`, `INSERT`, `UPDATE` on them. No `DELETE`, `DROP` or `ALTER`: the ledger never removes a row. The check reports each of the four as a yes or no.

## 4. Backup and rollback

* **Backup before:** the four tables are new, so there is no data to back up. Before applying, the check must show `tables_present` empty; if any exists, stop and look at it first.
* **Verify after:** the check again (`tables_present` lists all four; `grants` unchanged), and `SHOW CREATE TABLE` for each compared with the plan; row counts all 0.
* **Rollback:** `DROP TABLE` the four tables, allowed only while every row count is 0 (nothing reads the tables until the Inbox goes live, which is a later, separately approved step). A rollback needs the `DROP` privilege, which the account does not have by design: it is a one-time step by whoever administers the database, after Jim says so. If rows exist, rollback is not a drop: stop and ask.
* Nothing else changes: no existing table, row, grant or setting.

## 5. Isolation, secrets and access

* Every row names its `workspace` (personal or bytalos; TGP is refused earlier by the grant check), so Personal and Bytalos audit state sit in the same tables but are filtered by that column and by principal; a test (`tests/inbox/test_access.py`) proves a grant for one workspace never reads or lists the other.
* No secret or content is stored; `code_mac` is a keyed hash, the challenge code itself is never stored.
* Access is the existing V7 database account over the existing tunnel. No new login, no new network path.

## 6. Tests and what is not tested

* Done: the DDL equals the ledger's own schema; the ledger's claim-once, compare-and-set, replay and restart behaviour is tested over a file-backed SQL database with a new connection per call (`tests/inbox/test_durable.py`, `test_access.py`); the check module is tested against a canned server (flags only, read statements only).
* **Not done, and why:** no real MySQL or MariaDB ran here. There is no local server and the container runtime on this Mac is not running, so the DDL was not executed on MySQL itself; the first real execution is the approved apply on the target database. The read-only check runs on the real server before that and answers the row-format and privilege questions.

## 7. What Jim approves

Reply on LIFE-712 with: the environment (the existing V7 store), the database (the one named by `LIFEOS_ACQ_DB_NAME`), and "apply the four-table DDL in `docs/platform/inbox-schema-rollout.md`". Only after the check shows `ready: true` and no table present.
