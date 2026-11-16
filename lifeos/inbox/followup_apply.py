"""LIFE-756: the one-time apply of the five Followup tables (stage `followup-schema-apply`), after Jim approved the table family (D258, Nov 16 2026)."""
# It refuses unless `followup_schema.check` says ready and none of the five tables exists, runs the planned CREATE TABLE IF NOT EXISTS statements and nothing else, then reads the five tables back:
# present, the expected number of columns each, every table empty. Without SCHEMA_CONFIRM=APPLY (the manual diag workflow's confirm input) the stage only checks. Counts and flags only.
import os

from lifeos.inbox.followup_schema import TABLES, check, column_counts, plan
from lifeos.platform import db


class SchemaError(RuntimeError):
    """The message is always a fixed code."""


def apply(cursor):
    """Create the five tables (CREATE TABLE IF NOT EXISTS, nothing else) and read them back: all five present, the expected columns each, every table empty. Refuses unless `check` says ready and none of
    the five exists yet. -> counts and flags only."""
    before = check(cursor)
    if not before["ready"]:
        raise SchemaError("FOLLOWUP_SCHEMA_NOT_READY")
    if before["tables_present"]:
        raise SchemaError("FOLLOWUP_SCHEMA_TABLES_EXIST")
    for statement in plan():
        cursor.execute(statement)
    after = check(cursor)
    expected, columns, rows = column_counts(), {}, {}
    for table in TABLES:                                             # fixed names from followup_store, never from input
        cursor.execute(f"SHOW COLUMNS FROM {table}")
        columns[table] = len(cursor.fetchall())
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        rows[table] = int(cursor.fetchone()[0])
    verified = after["tables_present"] == sorted(TABLES) and columns == expected and not any(rows.values())
    if not verified:
        raise SchemaError("FOLLOWUP_SCHEMA_VERIFY_FAILED")
    return {"created": sorted(TABLES), "columns": columns, "rows": rows, "verified": True}


def apply_stage(limit, live):
    """The stage `followup-schema-apply`: it applies only when SCHEMA_CONFIRM is exactly APPLY (the manual diag workflow's confirm input); otherwise it only checks."""
    with db.connect() as connection, connection.cursor() as cursor:
        if os.environ.get("SCHEMA_CONFIRM") != "APPLY":
            return {"dry_run": True, "check": check(cursor)}
        return apply(cursor)


__all__ = ["SchemaError", "apply", "apply_stage"]
