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
DEFAULT_LIMIT = 200


def _status(notion, source_id, message):
    """-> (why, detail). why: recorded, no_order_id (unreadable), no_row (order never recorded), not_listed (order recorded, this message not on it).
    A message that carries several orders is recorded only when EVERY one of its orders lists it.
    detail (fixed vocabulary only): the parser's reason code for an unreadable message, or the order row's Status for a not_listed one."""
    extracted_all = events.extract_all(message)
    first = extracted_all[0]
    order_ids = [e.get("order_id") for e in extracted_all]
    if not all(order_ids):
        reason = first.get("reason") or "NONE"
        return "no_order_id", reason + (":" + events.ambiguity_shape(message) if reason == "ORDER_ID_AMBIGUOUS" else "")
    worst = ("recorded", "")
    for order_id in order_ids:
        rows = _query_order(notion, source_id, order_id)
        if not rows:
            return "no_row", ""
        if len(rows) == 1 and message["id"] in set(x for x in (rows[0].get("Source Message IDs") or "").splitlines() if x):
            continue
        worst = ("not_listed", str(rows[0].get("Status") or "NONE")[:30] if len(rows) == 1 else "DUPLICATE_ROWS")
    return worst


def run(limit, live, environ=os.environ, gmail=None, notion=None, now=None):
    counts = {"candidates": 0, "recorded": 0, "kept_unrecorded": 0, "no_order_id": 0, "no_row": 0, "not_listed": 0, "oldest_days": 0, "trashed": 0, "failed": 0}
    now = now or datetime.now(timezone.utc)
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
            counts["oldest_days"] = max(counts["oldest_days"], (now - datetime.fromisoformat(message["received_at"])).days)
            why, detail = _status(notion, source_id, message)
            if why != "recorded":
                counts["kept_unrecorded"] += 1
                counts[why] += 1
                bucket = counts.setdefault("kept_detail", {})
                bucket[f"{why}:{detail}"] = bucket.get(f"{why}:{detail}", 0) + 1
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
