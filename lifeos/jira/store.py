"""Private storage for the Jira snapshot: one row per project (see platform/snapshot_store)."""
from lifeos.platform.snapshot_store import Store

_STORE = Store("v7_jira_snapshot", "project_key", "VARCHAR(16)")
SCHEMA = _STORE.schema


def ensure_schema(connection):
    _STORE.ensure(connection)


def save(connection, snapshot):
    _STORE.save(connection, snapshot["project"], snapshot)


def load(connection, project):
    return _STORE.load(connection, project)
