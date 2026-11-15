"""LIFE-712: the one-time apply of the four Action Inbox tables (stage `inbox-schema-apply`), after Jim approved the exact DDL (Nov 15 2026).

It refuses unless `schema.check` says the server is ready and none of the four tables exists, runs the planned CREATE TABLE IF NOT EXISTS statements and nothing else, then reads the four tables back:
present, the expected number of columns each, every table empty. Without the confirmation word it only checks. Counts and flags only."""
import os

from lifeos.inbox import schema
from lifeos.platform import db


class SchemaError(RuntimeError):
    """The message is always a fixed code."""


def apply(cursor):
    """Create the four tables (CREATE TABLE IF NOT EXISTS, nothing else) and read them back: all four present, the expected number of columns each, every table empty. Refuses unless `check` says
    ready and none of the four exists yet. -> counts and flags only."""
    before = schema.check(cursor)
    if not before["ready"]:
        raise SchemaError("INBOX_SCHEMA_NOT_READY")
    if before["tables_present"]:
        raise SchemaError("INBOX_SCHEMA_TABLES_EXIST")
    for statement in schema.plan():
        cursor.execute(statement)
    after = schema.check(cursor)
    columns, rows = {}, {}
    for table in schema.TABLES:                                         # fixed names from this module, never from input
        cursor.execute(f"SHOW COLUMNS FROM {table}")
        columns[table] = len(cursor.fetchall())
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        rows[table] = int(cursor.fetchone()[0])
    verified = after["tables_present"] == sorted(schema.TABLES) and columns == schema.COLUMNS and not any(rows.values())
    if not verified:
        raise SchemaError("INBOX_SCHEMA_VERIFY_FAILED")
    return {"created": sorted(schema.TABLES), "columns": columns, "rows": rows, "verified": True}


def apply_stage(limit, live):
    """The stage `inbox-schema-apply`: it applies only when SCHEMA_CONFIRM is exactly APPLY (the manual diag workflow's confirm input); otherwise it only checks."""
    with db.connect() as connection, connection.cursor() as cursor:
        if os.environ.get("SCHEMA_CONFIRM") != "APPLY":
            return {"dry_run": True, "check": schema.check(cursor)}
        return apply(cursor)
