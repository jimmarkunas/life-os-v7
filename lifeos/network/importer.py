"""NET-1b: import the connections file attached to the private Notion page into the approved Hostinger tables.  python -m lifeos.run network-import

Dry (the default): counts only, no database connection, no table, no write. Live: create the tables if missing, record the batch, apply it in chunks (each chunk one transaction,
read back), mark it COMPLETE and read the totals back. Replaying the same file changes nothing. Email is never read into a record. Fixed NETWORK_* codes and counts only."""
import os
from datetime import date

from lifeos.platform import db
from lifeos.platform.notion_client import Client

from lifeos.network import identity, parse, source, store
from lifeos.network.errors import NetworkError


def run(limit, live, environ=os.environ, client=None, opener=None, connection=None, chunk=store.CHUNK):
    page_id = source.clean_page_id(environ.get("NETWORK_HANDOFF_PAGE_ID"))
    client = client or Client(environ, source_name="NETWORK_HANDOFF_PAGE_ID")
    blocks = source.csv_file_blocks(client, page_id)
    if not blocks:
        raise NetworkError("NETWORK_NO_FILE")
    chosen = source.newest(blocks)
    exported = source.export_date(chosen["name"]) or date.today().isoformat()
    rows, malformed = parse.read_rows(source.download(chosen["url"], *([opener] if opener else [])))
    plan = identity.classify(rows, malformed)
    counts = {"source_rows": plan["source_rows"], "people_accepted": len(plan["people"]), "positions_accepted": plan["positions"], "ambiguous_held": plan["ambiguous_held"],
              "rejected": plan["rejected"], "export_date": exported, "live": bool(live)}
    if not live:
        return counts
    key = identity.batch_key(plan, exported)
    if connection is not None:
        return _apply(counts, connection, key, plan, exported, chunk)
    with db.connect() as opened:
        return _apply(counts, opened, key, plan, exported, chunk)


def _apply(counts, connection, key, plan, exported, chunk):
    store.ensure_schema(connection)
    before = store.totals(connection)
    batch_id, status, cursor = store.open_batch(connection, key, plan, exported)
    counts["replay"] = status == "COMPLETE"
    if status != "COMPLETE":
        store.apply_batch(connection, batch_id, cursor, plan, exported, key[:16], chunk)
        store.complete(connection, batch_id, plan)
    after = store.totals(connection)
    counts.update(after)
    counts["people_new"], counts["positions_new"] = after["people_total"] - before["people_total"], after["positions_total"] - before["positions_total"]
    counts["batch_status"] = "COMPLETE"
    return counts


def audit(limit, live, connection=None):
    """python -m lifeos.run network-audit: read-only counts of the stored network (including how many text values look like an email address)."""
    if connection is not None:
        return store.audit(connection)
    with db.connect() as opened:
        return store.audit(opened)
