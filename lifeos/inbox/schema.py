"""LIFE-712: the exact schema the Action Inbox needs, and a read-only check of the existing V7 database before anyone creates it.

`plan()` is the DDL, taken from `ledger.SCHEMA` so the proposal and the code can never differ. `check(cursor)` runs five SELECT/SHOW statements and returns fixed flags and counts only (no database name, user,
host, grant text or row): server kind and version, the default row format (the largest unique key here is about 2,100 bytes, which needs DYNAMIC rows), which of the four tables already exist, and whether
the account may create and write them. The stage `inbox-schema-check` runs it; it creates nothing. Applying the DDL is a separate step that needs Jim's approval of the exact environment, database and DDL."""
import re

from lifeos.inbox.ledger import SCHEMA
from lifeos.platform import db

TABLES = ("v7_inbox_events", "v7_inbox_intents", "v7_inbox_actions", "v7_inbox_access")
NEEDED = ("CREATE", "SELECT", "INSERT", "UPDATE")              # no DELETE, DROP or ALTER: the ledger never removes a row
LARGEST_KEY_BYTES = 2096                                         # uq_inbox_action_slot: record_id 400 + action 40 + revision 80 + slot 4 characters at 4 bytes each, plus the slot flag


def plan():
    """The DDL statements in the order they run."""
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
    """-> counts and fixed flags only; a statement the server refuses is recorded as None, never as an error text."""
    out = {"server": None, "version": None, "row_format": None, "tables_present": [], "grants": None, "largest_key_bytes": LARGEST_KEY_BYTES}
    try:
        version = str(_one(cursor, "SELECT VERSION()") or "")
        out["server"] = "mariadb" if "mariadb" in version.lower() else "mysql"
        found = re.match(r"(\d+)\.(\d+)", version)
        out["version"] = f"{found.group(1)}.{found.group(2)}" if found else None
    except Exception:                                            # noqa: BLE001  a refused statement is a fixed None
        pass
    try:
        out["row_format"] = str(_one(cursor, "SELECT @@innodb_default_row_format")).lower()
    except Exception:                                            # noqa: BLE001
        pass
    try:
        cursor.execute("SHOW TABLES LIKE 'v7\\_inbox\\_%'")
        out["tables_present"] = sorted(str(row[0]) for row in cursor.fetchall() if str(row[0]) in TABLES)
    except Exception:                                            # noqa: BLE001
        pass
    try:
        out["grants"] = _grants(cursor)
    except Exception:                                            # noqa: BLE001
        pass
    out["ready"] = bool(out["row_format"] == "dynamic" and out["grants"] and all(out["grants"].values()))
    return out


def run(limit, live):
    """The stage: read-only, counts only. `live` changes nothing: this stage never writes."""
    with db.connect() as connection, connection.cursor() as cursor:
        return {"check": check(cursor), "tables_in_plan": len(plan())}
