"""One-time apply of the five Followup tables (stage `followup-schema-apply`).

Refuses unless `followup_schema.check` says ready and none of the five tables exists; runs the planned CREATE TABLE IF
NOT EXISTS statements and nothing else, then reads back column counts and emptiness. Without SCHEMA_CONFIRM=APPLY it
only checks.
"""

import os

from lifeos.inbox.followup_schema import TABLES, check, column_counts, plan
from lifeos.platform import db


class SchemaError(RuntimeError):
    """The message is always a fixed code."""


def apply(cursor):
    before = check(cursor)
    if not before["ready"]:
        raise SchemaError("FOLLOWUP_SCHEMA_NOT_READY")
    if before["tables_present"]:
        raise SchemaError("FOLLOWUP_SCHEMA_TABLES_EXIST")
    for statement in plan():
        cursor.execute(statement)
    after = check(cursor)
    expected, columns, rows = column_counts(), {}, {}
    for table in TABLES:
        cursor.execute(f"SHOW COLUMNS FROM {table}")
        columns[table] = len(cursor.fetchall())
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        rows[table] = int(cursor.fetchone()[0])
    verified = after["tables_present"] == sorted(TABLES) and columns == expected and not any(rows.values())
    if not verified:
        raise SchemaError("FOLLOWUP_SCHEMA_VERIFY_FAILED")
    return {"created": sorted(TABLES), "columns": columns, "rows": rows, "verified": True}


def apply_stage(limit, live):
    with db.connect() as connection, connection.cursor() as cursor:
        if os.environ.get("SCHEMA_CONFIRM") != "APPLY":
            return {"dry_run": True, "check": check(cursor)}
        return apply(cursor)


__all__ = ["SchemaError", "apply", "apply_stage"]
