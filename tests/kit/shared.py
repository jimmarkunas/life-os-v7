"""LIFE-544: small test doubles that several test files used to define byte-for-byte themselves. Domain-free: nothing here imports lifeos."""
import io
import json


class Reply:
    """A urlopen() response context whose read() returns `body` as JSON bytes."""

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.body).encode()


class Pushes:
    """A fake alert sender `(topic, title, body, severity) -> accepted`, recording every push."""

    def __init__(self, accept=True):
        self.sent, self.accept = [], accept

    def __call__(self, topic, title, body, severity):
        self.sent.append((topic, title, body, severity))
        return self.accept


class Clock:
    """A settable clock: call it for `now`."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Terminal(io.StringIO):
    """A text stream that claims to be a terminal."""

    def isatty(self):
        return True


class ConnWithCursor:
    """A connection whose cursor() returns the cursor it was built with."""

    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur


class SelfCursorDb:
    """A context-manager connection that is its own cursor (tests add execute/fetch methods on a subclass or use it bare)."""

    def __enter__(self): return self
    def __exit__(self, *e): return False
    def cursor(self): return self


class SqlLog(SelfCursorDb):
    """A connection that is its own cursor and records each statement as (its first word, args). `new` sets rowcount (an INSERT that took), `held` makes fetchone find a row."""

    def __init__(self, new=True, held=False): self.sql, self.rowcount, self.held = [], 1 if new else 0, held
    def execute(self, sql, args=()): self.sql.append((sql.split()[0], args))
    def fetchone(self): return (1,) if self.held else None
    def fetchall(self): return []


class Page:
    """A fetched page: html and status."""

    def __init__(self, html, status=200):
        self.html, self.status = html, status
