"""Observers for the db family: the real pymysql Cursor class (API only), the FakeCursor and MySQLite fakes. Domain-free."""
import inspect

from tests.kit.db import FakeCursor
from tests.kit.mysqlite import MySQLite

try:
    import pymysql.cursors as _real
except ImportError:                                                         # pymysql is a runtime dependency; the real-class checks skip without it
    _real = None


def observe_api(target):
    def observe(case):
        if "doc_of" in case:
            return {"mentions_affected_rows": "affected" in (inspect.getdoc(getattr(target, case["doc_of"])) or "").lower()}
        return {"present": sorted(name for name in case["api"] if hasattr(target, name))}
    return observe


def observe_pymysql(case):
    return observe_api(_real.Cursor)(case)


def _run(cursor, case):
    out = {}
    for op in case["ops"]:
        try:
            if "many" in op:
                value = cursor.executemany(op["many"], op["rows"])
                if value is None:
                    value = cursor.rowcount
            else:
                value = cursor.execute(op["sql"], op.get("args", ()))
                if op.get("fetch") == "one":
                    value = cursor.fetchone()
                elif op.get("fetch") == "all_types":
                    value = sorted({type(row).__name__ for row in cursor.fetchall()})
        except Exception as error:                                          # noqa: BLE001 - the case records which kind of error
            if "raises" in op:
                out[op["raises"]] = type(error).__name__.replace("sqlite3.", "")
                continue
            raise
        if "as" in op:
            out[op["as"]] = value
    return out


def observe_fake_cursor(case):
    if "api" in case:
        return observe_api(FakeCursor)(case)
    return _run(FakeCursor(rowcount=1), case)


def observe_mysqlite(case):
    if "api" in case:
        return observe_api(MySQLite().cursor().__class__)(case)
    return _run(MySQLite().cursor(), case)
