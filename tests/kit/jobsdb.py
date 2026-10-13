"""The Jobs store (every v7_* table, the real SQL of the stages) over in-memory sqlite, so a test runs the production statements instead of a canned reply.
Differences from MySQL that matter are handled here: MySQL's IF(), datetime/date columns read back as datetime/date, and COMMIT without a transaction."""
import sqlite3
from datetime import date, datetime

from lifeos.jobs import store
from tests.kit.mysqlite import MySQLite

sqlite3.register_adapter(datetime, lambda value: value.isoformat(" "))
sqlite3.register_adapter(date, lambda value: value.isoformat())
sqlite3.register_converter("DATETIME", lambda raw: datetime.fromisoformat(raw.decode()))
sqlite3.register_converter("DATE", lambda raw: date.fromisoformat(raw.decode()))


class JobsDB(MySQLite):
    def __init__(self):
        super().__init__()
        self.raw.close()                                                       # the base class opened a plain connection; this one parses column types
        self.raw = sqlite3.connect(":memory:", isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES)
        self.raw.create_function("IF", 3, lambda cond, a, b: a if cond else b)
        for statement in store.SCHEMA:
            self.cursor().execute(statement)
        for table, column, ddl, _ in store.COLUMNS:
            if column not in [r[1] for r in self.raw.execute(f"PRAGMA table_info({table})")]:          # v7_spend.browser_seconds is in both the CREATE and the list
                self.raw.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def insert(self, table, **columns):
        self.raw.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})", tuple(columns.values()))
