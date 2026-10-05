"""A MySQL-shaped connection over in-memory sqlite, so the Network store's real SQL (inserts, ignores, joins, transactions) runs in tests. Only the dialect differences the store uses are translated:
%s placeholders, INSERT IGNORE, AUTO_INCREMENT keys, UNIQUE KEY / KEY clauses and the ENGINE suffix. begin/commit/rollback are real, so a failed chunk really rolls back."""
import re
import sqlite3


def translate_ddl(sql):
    sql = re.sub(r"ENGINE=.*$", "", sql.strip())
    sql = re.sub(r"BIGINT AUTO_INCREMENT PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT", sql)
    sql = re.sub(r",\s*UNIQUE KEY \w+ \(([^)]*)\)", r", UNIQUE (\1)", sql)
    sql = re.sub(r",\s*KEY \w+ \([^)]*\)", "", sql)
    return sql


def translate(sql):
    sql = sql.replace("%s", "?").replace("INSERT IGNORE", "INSERT OR IGNORE")
    if sql.lstrip().upper().startswith("CREATE TABLE"):
        sql = translate_ddl(sql)
    return sql


class Cursor:
    def __init__(self, conn):
        self.cur = conn.raw.cursor()
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=()):
        self.conn.maybe_fail(sql)
        self.cur.execute(translate(sql), tuple(args))

    def executemany(self, sql, rows):
        self.conn.maybe_fail(sql)
        self.cur.executemany(translate(sql), [tuple(r) for r in rows])

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()


class MySQLite:
    def __init__(self):
        self.raw = sqlite3.connect(":memory:", isolation_level=None)
        self.fail_on = None
        self.statements = []

    def maybe_fail(self, sql):
        self.statements.append(sql.split()[0] + " " + sql.split()[1] if len(sql.split()) > 1 else sql)
        if self.fail_on and self.fail_on[0] in sql:
            self.fail_on = (self.fail_on[0], self.fail_on[1] - 1)
            if self.fail_on[1] == 0:
                self.fail_on = None
                raise RuntimeError("simulated database failure")

    def cursor(self):
        return Cursor(self)

    def begin(self):
        self.raw.execute("BEGIN")

    def commit(self):
        self.raw.execute("COMMIT")

    def rollback(self):
        if self.raw.in_transaction:
            self.raw.execute("ROLLBACK")

    def count(self, table):
        return self.raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
