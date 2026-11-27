"""The schema the Action Inbox needs, and a read-only check of the V7 database before it is created.

`plan()` is the DDL, taken from `ledger.SCHEMA`. `check(cursor)` runs five SELECT/SHOW statements and returns fixed
flags and counts only: server version, default row format (the largest unique key is about 2,100 bytes, so rows must
be DYNAMIC), which of the four tables exist, and whether the account may create and write them. The apply is
`apply_schema`.
"""

import re

from lifeos.inbox.ledger import ACCESS_COLUMNS, ACTION_COLUMNS, INTENT_COLUMNS, SCHEMA
from lifeos.platform import db

TABLES = ("v7_inbox_events", "v7_inbox_intents", "v7_inbox_actions", "v7_inbox_access")
NEEDED = ("CREATE", "SELECT", "INSERT", "UPDATE")
COLUMNS = {
    "v7_inbox_events": 3,
    "v7_inbox_intents": len(INTENT_COLUMNS),
    "v7_inbox_actions": len(ACTION_COLUMNS),
    "v7_inbox_access": len(ACCESS_COLUMNS),
}
# uq_inbox_action_slot: record_id 400 + action 40 + revision 80 + slot 4 characters at 4 bytes each, plus the slot
# flag
LARGEST_KEY_BYTES = 2096


def plan():
    return list(SCHEMA)


def _one(cursor, sql):
    cursor.execute(sql)
    row = cursor.fetchone()
    return row[0] if row else None


def _grants(cursor):
    cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
    text = " ".join(str(row[0]).upper() for row in cursor.fetchall())
    everything = "ALL PRIVILEGES" in text
    return {name: everything or bool(re.search(rf"\b{name}\b", text)) for name in NEEDED}


def check(cursor):
    out = {
        "server": None,
        "version": None,
        "row_format": None,
        "tables_present": [],
        "grants": None,
        "largest_key_bytes": LARGEST_KEY_BYTES,
    }
    try:
        version = str(_one(cursor, "SELECT VERSION()") or "")
        out["server"] = "mariadb" if "mariadb" in version.lower() else "mysql"
        found = re.match(r"(\d+)\.(\d+)", version)
        out["version"] = f"{found.group(1)}.{found.group(2)}" if found else None
    except Exception:  # noqa: BLE001  a refused statement is a fixed None
        pass
    try:
        out["row_format"] = str(_one(cursor, "SELECT @@innodb_default_row_format")).lower()
    except Exception:  # noqa: BLE001
        pass
    try:
        cursor.execute("SHOW TABLES LIKE 'v7\\_inbox\\_%'")
        out["tables_present"] = sorted(str(row[0]) for row in cursor.fetchall() if str(row[0]) in TABLES)
    except Exception:  # noqa: BLE001
        pass
    try:
        out["grants"] = _grants(cursor)
    except Exception:  # noqa: BLE001
        pass
    out["ready"] = bool(out["row_format"] == "dynamic" and out["grants"] and all(out["grants"].values()))
    return out


def run(limit, live):
    with db.connect() as connection, connection.cursor() as cursor:
        return {"check": check(cursor), "tables_in_plan": len(plan())}
