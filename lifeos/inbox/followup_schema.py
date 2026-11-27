"""Read-only check of the V7 database for the five Followup tables (stage `followup-schema-check`) and the DDL plan.

`check(cursor)` runs five SELECT/SHOW statements and returns fixed flags and counts only: server version, row format,
which tables exist, whether the account may create and write them, and whether the largest key (computed from the DDL)
fits the row format. It creates nothing; the apply is `followup_apply`.
"""

import re

from lifeos.inbox import followup_store
from lifeos.platform import db

TABLES = followup_store.TABLES
NEEDED = ("CREATE", "SELECT", "INSERT", "UPDATE")
KEY_LIMITS = {"dynamic": 3072, "compressed": 3072, "compact": 767, "redundant": 767}
_COLUMN = re.compile(r"(\w+)\s+(CHAR|VARCHAR)\((\d+)\)")
_KEY = re.compile(r"\bKEY\s*(?:\w+\s*)?\(([^)]*)\)")


def plan():
    return list(followup_store.SCHEMA)


def _table(statement):
    return statement.split("EXISTS ")[1].split(" ")[0]


def _parts(statement):
    body = statement[statement.index("(") + 1 : statement.rindex(")")]
    parts, depth, current = [], 0, ""
    for char in body:
        depth += (char == "(") - (char == ")")
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    return parts + [current.strip()]


def column_counts():
    """Returns {table: number of columns}, counted from the DDL (key definitions are not columns)."""
    return {_table(s): sum(1 for p in _parts(s) if not re.match(r"(PRIMARY|UNIQUE|KEY)\b", p)) for s in plan()}


def key_bytes():
    """Returns {table: largest key in bytes at 4 bytes per utf8mb4 character, plus 1 or 2 length bytes for a VARCHAR}."""
    out = {}
    for statement in plan():
        widths = {}
        for name, kind, size in _COLUMN.findall(statement):
            chars = int(size) * 4
            widths[name] = chars + (0 if kind == "CHAR" else (1 if chars <= 255 else 2))
        keys = [sum(widths[c.strip()] for c in cols.split(",")) for cols in _KEY.findall(statement)]
        out[_table(statement)] = max(keys)
    return out


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
    largest = max(key_bytes().values())
    out = {
        "server": None,
        "version": None,
        "row_format": None,
        "tables_present": [],
        "grants": None,
        "largest_key_bytes": largest,
        "key_limit_bytes": None,
        "keys_fit": None,
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
        out["key_limit_bytes"] = KEY_LIMITS.get(out["row_format"])
        out["keys_fit"] = None if out["key_limit_bytes"] is None else largest <= out["key_limit_bytes"]
    except Exception:  # noqa: BLE001
        pass
    try:
        cursor.execute("SHOW TABLES LIKE 'v7\\_followup\\_%'")
        out["tables_present"] = sorted(str(row[0]) for row in cursor.fetchall() if str(row[0]) in TABLES)
    except Exception:  # noqa: BLE001
        pass
    try:
        out["grants"] = _grants(cursor)
    except Exception:  # noqa: BLE001
        pass
    out["ready"] = bool(out["keys_fit"] and out["grants"] and all(out["grants"].values()))
    return out


def run(limit, live):
    with db.connect() as connection, connection.cursor() as cursor:
        return {"check": check(cursor), "tables_in_plan": len(plan())}


__all__ = ["plan", "check", "run", "key_bytes", "column_counts", "TABLES"]
