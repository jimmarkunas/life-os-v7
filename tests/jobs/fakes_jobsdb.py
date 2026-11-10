"""The Jobs store (every v7_* table, the real SQL of the stages) over in-memory sqlite, so a test runs the production statements instead of a canned reply.
Differences from MySQL that matter are handled here: MySQL's IF(), datetime/date columns read back as datetime/date, and COMMIT without a transaction."""
import re
import sqlite3
import zlib
from datetime import date, datetime, timezone

from lifeos.jobs import store
from tests.kit.mysqlite import Cursor, MySQLite, translate

sqlite3.register_adapter(datetime, lambda value: value.isoformat(" "))
sqlite3.register_adapter(date, lambda value: value.isoformat())
sqlite3.register_converter("DATETIME", lambda raw: datetime.fromisoformat(raw.decode()))
sqlite3.register_converter("DATE", lambda raw: date.fromisoformat(raw.decode()))


def mysql_to_sqlite(sql, args):
    """The MySQL spellings the stages' own statements use, as sqlite understands them: a tuple parameter is a parenthesised list (`IN %s`, as PyMySQL renders it), ON DUPLICATE KEY UPDATE is an upsert on
    the table's one key, and a multi-table UPDATE or DELETE becomes UPDATE ... FROM or a rowid subquery."""
    flat, parts = [], sql.split("%s")
    sql = parts[0]
    for part, arg in zip(parts[1:], args):
        many = isinstance(arg, (tuple, list))
        sql += ("(" + ",".join(["%s"] * len(arg)) + ")" if many else "%s") + part
        flat.extend(arg if many else [arg])
    sql = re.sub(r"ON DUPLICATE KEY UPDATE\s+(.*)$", lambda m: "ON CONFLICT DO UPDATE SET " + re.sub(r"VALUES\((\w+)\)", r"excluded.\1", m.group(1)), sql, flags=re.S)
    update = re.match(r"\s*UPDATE (\w+) (\w+) JOIN (\w+) (\w+) ON (.+?) SET (.+?) WHERE (.+)$", sql, re.S)
    if update:
        table, alias, other, other_alias, on, sets, where = update.groups()
        sql = f"UPDATE {table} AS {alias} SET {re.sub(rf'\b{alias}\.', '', sets)} FROM {other} AS {other_alias} WHERE {on} AND ({where})"
    delete = re.match(r"\s*DELETE (\w+) FROM (\w+) \1 JOIN (.+)$", sql, re.S)
    if delete:
        alias, table, rest = delete.groups()
        sql = f"DELETE FROM {table} WHERE rowid IN (SELECT {alias}.rowid FROM {table} {alias} JOIN {rest})"
    return sql, tuple(flat)


class JobsCursor(Cursor):
    def execute(self, sql, args=()):
        self.conn.maybe_fail(sql)
        sql, args = mysql_to_sqlite(sql, tuple(args))
        self.cur.execute(translate(sql), args)


class JobsDB(MySQLite):
    def __init__(self):
        super().__init__()
        self.raw.close()                                                       # the base class opened a plain connection; this one parses column types
        self.raw = sqlite3.connect(":memory:", isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES)
        self.raw.create_function("IF", 3, lambda cond, a, b: a if cond else b)
        self.raw.create_function("UTC_TIMESTAMP", 0, lambda: datetime.now(timezone.utc).replace(tzinfo=None).isoformat(" "))      # the MySQL functions the stages' statements call
        self.raw.create_function("HOUR", 1, lambda value: datetime.fromisoformat(str(value)).hour if value else None)
        self.raw.create_function("CRC32", 1, lambda value: zlib.crc32(str(value).encode()))
        self.raw.create_function("MOD", 2, lambda a, b: a % b)
        self.raw.create_function("LEAST", -1, lambda *values: min(v for v in values if v is not None))
        self.raw.create_function("CONCAT", -1, lambda *values: "".join(str(v) for v in values))
        self.raw.create_function("REGEXP", 2, lambda pattern, value: bool(re.search(pattern, value or "", re.I)))                 # MySQL's REGEXP ignores case; sqlite passes the pattern first
        for statement in store.SCHEMA:
            self.cursor().execute(statement)
        for table, column, ddl, _ in store.COLUMNS:
            if column not in [r[1] for r in self.raw.execute(f"PRAGMA table_info({table})")]:          # v7_spend.browser_seconds is in both the CREATE and the list
                self.raw.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def cursor(self):
        return JobsCursor(self)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def insert(self, table, **columns):
        self.raw.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})", tuple(columns.values()))
