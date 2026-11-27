"""Synthetic Action Inbox fixtures: fake native records for the six sources in two workspaces, fake sources and a fake
executor.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.inbox import policy
from lifeos.inbox.ledger import Ledger
from tests.kit.mysqlite import MySQLite

CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 14, 14, 0, tzinfo=CT)
KEY = b"synthetic-test-key-not-a-secret!!"
CANARY = "CANARY-TEXT-7731"
ERROR_CANARY = "CANARY-ERROR-5521"

SAME_TITLE = "Follow up with the vendor"


def raw(source, workspace):
    """One native record per source and workspace, in each source's own field names."""
    p = "personal" if workspace == "personal" else "bytalos"
    base = {"updated": "2026-11-14T13:30:00", "tz": "America/Chicago", "url": f"https://example.test/{source}/{p}/1"}
    return {
        "jira": {
            **base,
            "key": f"LIFE-{1 if p == 'personal' else 2}",
            "summary": f"{SAME_TITLE} {CANARY}",
            "description": CANARY,
            "assignee": "jim",
            "status": "Human UAT",
            "evidence": [f"https://example.test/pr/{p}"],
        },
        "todoist": {
            **base,
            "id": f"t{1 if p == 'personal' else 2}",
            "content": SAME_TITLE,
            "description": CANARY,
            "assignee": "jim",
            "status": "open",
            "due": "2026-11-14T09:00:00",
            "project_scope": p,
        },
        "concord": {
            **base,
            "id": f"c{1 if p == 'personal' else 2}",
            "title": SAME_TITLE,
            "body": CANARY,
            "agent": "claude-code",
            "status": "pending_approval",
            "evidence": [f"concord://evidence/{p}"],
            "correlation_id": f"corr-{p}",
        },
        "followup": {
            **base,
            "id": f"f{1 if p == 'personal' else 2}",
            "title": SAME_TITLE,
            "notes": CANARY,
            "owner": "jim",
            "status": "open",
            "next_checkpoint": "2026-11-14T10:00:00",
        },
        "calendar": {
            **base,
            "id": f"e{1 if p == 'personal' else 2}",
            "summary": SAME_TITLE,
            "description": CANARY,
            "status": "confirmed",
            "start": "2026-11-14T16:00:00",
        },
        "asana": {
            **base,
            "gid": f"a{1 if p == 'personal' else 2}",
            "name": SAME_TITLE,
            "notes": CANARY,
            "assignee": "jim",
            "status": "open",
            "due": "2026-11-15T09:00:00",
        },
    }[source]


class FakeSource:
    def __init__(self, source, workspace, account=None, rows=None, error=None):
        self.source, self.workspace = source, workspace
        self.account = account or f"jim-{workspace}"
        self.identity = "jim"
        self.rows = [raw(source, workspace)] if rows is None else rows
        self.error = error
        self.calls = []  # the credential NAMES this source was fetched with

    def fetch(self, credential_ref):
        self.calls.append(credential_ref)
        if self.error:
            raise self.error
        return self.rows


def all_sources(workspaces=("personal", "bytalos"), **kw):
    return [FakeSource(s, w, **kw) for w in workspaces for s in policy.MATRIX]


def grants(workspaces=("personal", "bytalos"), sources=None, expires=None, revoked=(), actions=None):
    out = []
    for w in workspaces:
        for s, allowed in policy.MATRIX.items():
            if sources and s not in sources:
                continue
            out.append(
                policy.Grant(
                    s,
                    w,
                    f"jim-{w}",
                    frozenset(actions.get(s, allowed) if actions else allowed),
                    f"cred:{s}:{w}",
                    expires or NOW + timedelta(days=1),
                    revoked=(s, w) in revoked,
                )
            )
    return tuple(out)


def principal(**kw):
    return policy.Principal("jim", grants(**kw))


def record_for(inbox, source, workspace):
    return next(r for r in inbox.records if r.source == source and r.workspace == workspace)


class Executor:
    """A fake source system."""

    def __init__(self, status=None, raises=None, revision="r2"):
        self.calls, self.status, self.raises, self.revision = [], status, raises, revision

    def __call__(self, record, action):
        self.calls.append((record.record_id, action))
        if self.raises:
            raise self.raises
        expected = sorted(policy.EXPECTED_STATUS[action])[0]
        return {"record_id": record.record_id, "revision": self.revision, "source_status": self.status or expected}


class FileDb(MySQLite):
    """The MySQL-shaped test connection over a sqlite FILE: every worker opens its own connection to the same file, so
    claims race for real and survive a.
    """

    def __init__(self, path):
        self.raw = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
        self.fail_on, self.statements = None, []


def memory_ledger():
    """A ledger over one shared in-memory database (one process, no restart)."""
    shared = MySQLite()

    @contextmanager
    def connect():
        yield shared

    ledger = Ledger(connect)
    ledger.ensure_schema()
    return ledger


def file_ledger(path, create=True):
    """A ledger over the file at `path`: a new call is a new worker (or a restarted process) seeing the same durable
    state.
    """

    @contextmanager
    def connect():
        connection = FileDb(path)
        try:
            yield connection
        finally:
            connection.raw.close()

    ledger = Ledger(connect)
    if create:
        ledger.ensure_schema()
    return ledger
