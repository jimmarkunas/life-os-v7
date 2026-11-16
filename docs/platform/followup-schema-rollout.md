# Followup tables: check, apply and rollback (LIFE-756, Nov 16 2026)

Under LIFE-696 / LIFE-737. Authority: Jim approved the `v7_followup_*` table family (D258), said "Go on all 3" (tables, source credential grants, the workflow step) at 3:15 PM CT, and in the complete-build scope addition added a fifth table, `v7_followup_payload`. Code: `lifeos/inbox/followup_store.py` (the DDL and the only writers), `lifeos/inbox/followup_schema.py` (stage `followup-schema-check`), `lifeos/inbox/followup_apply.py` (stage `followup-schema-apply`), tests `tests/inbox/test_followup_schema.py` and `tests/inbox/test_followup_store.py`. Merging the PR that holds this file creates no table.

## The five tables

| Table | Key | Holds |
|---|---|---|
| `v7_followup_threads` | PK `thread_id` (256 B); index `(workspace, account, resolution)` (444 B) | one open-loop thread: derived state, checkpoint, source links, evidence fingerprint |
| `v7_followup_checkpoints` | PK `(workspace, account, source)` (468 B) | one cursor per scope and source, advanced only after an accepted write |
| `v7_followup_overrides` | PK `thread_id` (256 B) | a Jim checkpoint override; never edits a source due date |
| `v7_followup_suppressions` | PK `(thread_id, fingerprint)` (512 B) | fingerprints of dismissed or closed evidence |
| `v7_followup_payload` | PK `scope_key` VARCHAR(190) (762 B) | ONE component-state payload per (workspace, account), replaced each live run and read back (`save_payload` / `load_payload`) |

All are `CREATE TABLE IF NOT EXISTS`, InnoDB, utf8mb4. They hold ids, fixed words, hashes, UTC times, bounded labels, source links and Followup's own derived payload: never a mail body, a copied source payload, a credential or an error text.

## Row format and key size (computed from the DDL by the check)

The largest key is `v7_followup_payload.scope_key` at 762 bytes (190 characters at 4 bytes, plus 2 length bytes). That is under the 767-byte limit of a COMPACT or REDUNDANT row format, and far under the 3,072 bytes of DYNAMIC, so the key size does not depend on the server's default row format. The check still reports the row format and `keys_fit`; an unknown row format is "not ready" because it cannot be judged.

## Privileges

`CREATE` (once), `SELECT`, `INSERT`, `UPDATE`. No `DELETE`, `DROP` or `ALTER`. The payload row is replaced with `INSERT ... ON DUPLICATE KEY UPDATE`, never `REPLACE`, which would need `DELETE`.

## Running it (manual `diag` workflow, one step at a time)

1. Read-only check; creates nothing: `gh workflow run diag.yml --ref main -f stage=followup-schema-check`
2. Read the run's log: `ready` must be true and `tables_present` empty. If not, stop and look; do not work around it.
3. Apply (creates the five tables once and reads them back): `gh workflow run diag.yml --ref main -f stage=followup-schema-apply -f confirm=APPLY`. Without `confirm` exactly `APPLY` the stage is a dry run that only checks.
4. Read-back: all five tables present, the expected columns each (16, 5, 4, 4, 4), every table empty. Any mismatch fails the stage with a fixed code (`FOLLOWUP_SCHEMA_VERIFY_FAILED`). `FOLLOWUP_SCHEMA_NOT_READY` and `FOLLOWUP_SCHEMA_TABLES_EXIST` refuse before any statement runs.

## Rollback

The tables are new and empty, so there is no data to back up. `DROP TABLE` each of the five is allowed only while every row count is 0, and needs the `DROP` privilege the account does not have by design: it is a one-time step by whoever administers the database, after Jim says so. If rows exist, rollback is not a drop: stop and ask. Nothing else changes: no existing table, row, grant or setting.
