"""LIFE-384 (D250): remember the id of a Notion page V7 created, under a fixed key, in the private store, and read it back by id (never by searching or matching a title).

Same pattern as LIFE-378's private report pages: one small private-store table (CREATE TABLE IF NOT EXISTS, replace, read-back); the stored page id is read with GET /pages/{id}; an archived or
missing page means "write afresh". A dry run never creates the table. Fixed codes only."""
from datetime import datetime, timezone

from lifeos.platform import snapshot_store
from lifeos.platform.notion_client import NotionError

STORE = snapshot_store.Store("v7_private_reports", key_column="report_key", key_type="VARCHAR(96)")     # the table LIFE-378 created; keys here are prefixed "dash:"


def find(client, connect, key, live):
    """The live page id stored under `key`, else None. A dry run reads only (a missing table means no page yet)."""
    with connect() as connection:
        if live:
            STORE.ensure(connection)
            saved = STORE.load(connection, key)
        else:
            try:
                saved = STORE.load(connection, key)
            except Exception:                                         # noqa: BLE001  the table does not exist before the first live run
                saved = None
    page_id = (saved or {}).get("page_id")
    if not page_id:
        return None
    page = client.call("GET", f"/pages/{page_id}")
    return None if page.get("archived") or page.get("in_trash") else page_id


def remember(connect, key, page_id):
    with connect() as connection:
        STORE.ensure(connection)
        snapshot = {"taken_at": datetime.now(timezone.utc).isoformat(), "schema": 1, "page_id": page_id}
        STORE.save(connection, key, snapshot)
        if STORE.load(connection, key) != snapshot:
            raise NotionError("PRIVATE_REPORT_KEY_READBACK")
