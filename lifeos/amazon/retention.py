"""D139: Amazon order mail older than 30 days goes to the Gmail Trash, once its order is safely recorded in the canonical Amazon Orders data source.

Never permanent: the Trash keeps a message another 30 days (Gmail's own rule), so a mistake is recoverable. Only the three allowlisted senders, only mail already
under the Amazon label (a Gmail filter files it there; V7 does not depend on who filed it), and only mail whose message id is on its order row (Source Message IDs).
Anything else is counted and left alone. Dry run unless live; counts only."""
import os
from datetime import datetime, timezone

from lifeos.platform.gmail import Gmail, GmailError
from . import events
from .stage import AmazonError, SENDERS_QUERY, _config, _query_order, _schema

RETENTION_DAYS = 30
DEFAULT_LIMIT = 100


def _recorded(notion, source_id, message):
    parsed = events.extract(message)
    order_id = parsed.get("order_id")
    if not order_id:
        return False
    rows = _query_order(notion, source_id, order_id)
    return len(rows) == 1 and message["id"] in set(x for x in (rows[0].get("Source Message IDs") or "").splitlines() if x)


def run(limit, live, environ=os.environ, gmail=None, notion=None, now=None):
    counts = {"candidates": 0, "recorded": 0, "kept_unrecorded": 0, "trashed": 0, "failed": 0}
    try:
        gmail = gmail or Gmail.from_env()
        source_id, notion = _config(environ) if notion is None else ((environ.get("NOTION_AMAZON_DATA_SOURCE_ID") or "").strip(), notion)
        if not source_id:
            raise AmazonError("AMAZON_CONFIG_MISSING")
        _schema(notion, source_id)
        gmail.label_id("Amazon", create=False)                                                      # the label must exist; it is never created here
        query = f"{SENDERS_QUERY} label:Amazon older_than:{RETENTION_DAYS}d"
        ids = gmail.list_ids(query, limit if limit and limit > 0 else DEFAULT_LIMIT)
        counts["candidates"] = len(ids)
        for message_id in ids:
            message = gmail.message_record(message_id)
            if "TRASH" in message["label_ids"]:
                continue
            if not _recorded(notion, source_id, message):
                counts["kept_unrecorded"] += 1
                continue
            counts["recorded"] += 1
            if not live:
                continue
            try:
                gmail.trash(message_id)
                counts["trashed"] += 1
            except GmailError:
                counts["failed"] += 1
    except (GmailError, AmazonError) as error:
        counts["failed"] += 1
        counts["error"] = str(error)[:60]
    return counts
