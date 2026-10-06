"""The one private derivative row that carries the Hiring Pipeline between runs (one table, one key, replaced each run, saved with read-back).

It holds only what safe carry-forward and freshness need: the compiled rows (structure, never display text) and when state was last fully accepted. It is a cache of
projection state, not a hiring database: the canonical sources are the Job Ledger, the Notion pages and the Calendar."""
from datetime import datetime

from lifeos.platform.notion_client import NotionError
from lifeos.platform.snapshot_store import Store

STORE, KEY, SCHEMA_V = Store("v7_hiring_pipeline"), 1, 1


class HiringError(NotionError):
    """Fixed codes only: no company, role, id or message ever enters one."""


def fail(code):
    return HiringError("HIRING_" + code)


def build(result, prior, now):
    """-> the snapshot to store, or None when there is nothing safe to keep (no rows and no prior)."""
    rows = result["rows"] if result["rows"] is not None else (prior or {}).get("rows")
    if rows is None:
        return None
    clean = not result["reasons"]
    accepted = now.isoformat() if clean else (prior or {}).get("accepted_at")
    return {"schema": SCHEMA_V, "taken_at": now.isoformat(), "accepted_at": accepted, "reasons": result["reasons"], "rows": rows}


def load(connection):
    try:
        stored = STORE.load(connection, KEY)
    except (TypeError, ValueError):
        raise fail("SNAPSHOT_INVALID") from None
    if stored is not None and (stored.get("schema") != SCHEMA_V or not isinstance(stored.get("rows"), list)):
        raise fail("SNAPSHOT_INVALID")
    return stored


def save(connection, snapshot):
    STORE.save_verified(connection, KEY, snapshot, fail)
    datetime.fromisoformat(snapshot["taken_at"])
