class FakeCursor:
    """Records every statement as (first two words, args). `script` maps an SQL prefix to the row fetchone() returns;
    `handler(sql, args, cursor)` may set cursor.rowcount / cursor.row for anything more involved."""

    def __init__(self, script=None, handler=None, rowcount=1):
        self.script, self.handler, self.rowcount = script or {}, handler, rowcount
        self.sql, self.row = [], None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=()):
        self.sql.append((" ".join(sql.split()[:2]), args))
        self.row = next((row for prefix, row in self.script.items() if sql.startswith(prefix)), None)
        if self.handler:
            self.handler(sql, args, self)
        return self.rowcount                             # LIFE-549: like pymysql, execute() returns the affected-row count

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [self.row] if self.row else []

    def verbs(self):
        return [words.split()[0] for words, _ in self.sql]


class FakeConn:
    """One shared cursor, so a test can read what every statement did. Also a context manager like store.connect()."""

    def __init__(self, script=None, handler=None):
        self.cur = FakeCursor(script, handler)

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RoutedRows:
    """A connection that is its own cursor (and a context manager, like store.connect()): execute() keeps the statement, then fetchall() returns the canned rows of
    the FIRST route (needle, rows) whose needle is inside it, else `default`."""

    def __init__(self, routes=(), default=()):
        self.routes, self.default, self.sql, self.rows = list(routes), default, [], default

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, args=()):
        self.sql.append(sql)
        self.rows = next((rows for needle, rows in self.routes if needle in sql), self.default)

    def fetchall(self):
        return self.rows

    def verbs(self):
        return [sql.split()[0] for sql in self.sql]


class RecorderCursor:
    """LIFE-551: keeps every statement in full as (sql, args) in `statements`; fetchall() returns the canned `rows`; execute() raises the error whose
    needle is inside the statement (after recording it); `driver_format=True` also checks the statement survives pymysql-style % formatting first."""

    def __init__(self, rows=(), errors=None, driver_format=False):
        self.rows, self.errors, self.driver_format, self.statements = list(rows), errors or {}, driver_format, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=()):
        if self.driver_format:
            sql % tuple("x" for _ in args)
        self.statements.append((sql, args))
        for needle, error in self.errors.items():
            if needle in sql:
                raise error

    def fetchall(self):
        return self.rows


class RecorderConn:
    """One RecorderCursor behind a connection that is also a context manager, like store.connect()."""

    def __init__(self, rows=(), errors=None, driver_format=False):
        self.cur = RecorderCursor(rows, errors, driver_format)

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SingleRowDb:
    """LIFE-551: one row in memory behind the statements the two stuck-sync watches run (a SELECT returns the row; an INSERT stores params 1 to 3 as the new row).
    `sql` keeps the first word of each statement; `broken=True` makes entering the connection raise like a lost store tunnel."""

    def __init__(self, row=None, broken=False):
        self.row, self.broken, self.sql = row, broken, []
        self._last = None

    def __enter__(self):
        if self.broken:
            raise OSError("STORE_SSH_NETWORK")
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        self.sql.append(sql.split()[0])
        if sql.startswith("SELECT"):
            self._last = self.row
        elif sql.startswith("INSERT"):
            self.row = (params[1], params[2], params[3])

    def fetchone(self):
        return self._last


class BillsSnapshotDB:
    """Private snapshot database fake with one atomically replaced JSON payload."""

    def __init__(self, payload=None):
        import json
        self.payload = json.dumps(payload) if payload is not None else None
        self.sql = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=()):
        self.sql.append(sql)
        if sql.startswith("INSERT INTO v7_bills_snapshot"):
            self.payload = args[-1]

    def fetchone(self):
        return (self.payload,) if self.payload is not None else None


class AgendaSnapshotDB:
    """Private Agenda snapshot row with an atomic JSON replacement for tests."""

    def __init__(self, snapshot=None):
        import json
        self.payload = json.dumps(snapshot) if snapshot is not None else None
        self.sql = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=()):
        self.sql.append(sql)
        if sql.startswith("INSERT INTO v7_bills_snapshot") or sql.startswith("INSERT INTO v7_agenda_snapshot"):
            self.payload = args[-1]

    def fetchone(self):
        return (self.payload,) if self.payload is not None else None


class BillsPaidDB:
    """Write-ahead ledger fake with one-shot crash injection at a named saved step."""

    def __init__(self, event_log=None, fail_step=None):
        import json
        self.json = json
        self.rows = {}
        self.event_log = event_log if event_log is not None else []
        self.fail_step, self.failed = fail_step, False
        self.row, self.rows_result = None, []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=()):
        if sql.startswith("CREATE TABLE"):
            return
        if sql.startswith("INSERT INTO v7_bills_paid_ledger"):
            entry = self.json.loads(args[-1])
            self.event_log.append(("ledger", entry["step"]))
            if self.fail_step == entry["step"] and not self.failed:
                self.failed = True
                raise RuntimeError("synthetic interruption")
            self.rows[args[0]] = args[-1]
            self.row = (args[-1],)
            return
        if sql.startswith("SELECT page_id, payload FROM v7_bills_paid_ledger"):
            self.rows_result = list(self.rows.items())
            self.row = None
            return
        if sql.startswith("SELECT payload FROM v7_bills_paid_ledger"):
            self.row = (self.rows[args[0]],) if args[0] in self.rows else None
            return
        raise AssertionError(sql)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return list(self.rows_result)
