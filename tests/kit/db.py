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

    def fetchone(self):
        return self.row

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
