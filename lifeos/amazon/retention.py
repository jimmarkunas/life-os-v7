"""D139: Amazon order mail older than 30 days goes to the Gmail Trash, once its order is safely recorded in the canonical Amazon Orders data source.

Never permanent: the Trash keeps a message another 30 days (Gmail's own rule), so a mistake is recoverable. Only the three allowlisted senders, only mail already
under the Amazon label (a Gmail filter files it there; V7 does not depend on who filed it), and only mail whose message id is on its order row (Source Message IDs).
Anything else is counted and left alone. Dry run unless live; counts only."""
import hashlib
import os
from datetime import datetime, timezone

from lifeos.platform import mail_checkpoint
from lifeos.platform.gmail import Gmail, GmailError
from . import events
from .stage import AmazonError, SENDERS_QUERY, _config, _query_order, _schema, tally

RETENTION_DAYS = 30
REVIEW_DAYS = 90                      # Jim, Oct 6: mail for an order held in REVIEW is trashable once it is 90 days old (the Trash keeps it 30 more)
DEFAULT_LIMIT = 200


READER = "amazon_retention"


def parser_version():
    """-> a short fingerprint of the order parser's own source: a changed parser makes every saved digest stale, so the mail is read and judged again."""
    path = events.__file__
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()[:16]


def _digest(message):
    """LIFE-906: what the parser concluded about one mail, as small JSON (no body, subject or address): the received time, each event's order id, reason, status and whether it carried a total, and the shape counts of an ambiguous one."""
    found = events.extract_all(message)
    return {"received_at": message["received_at"], "shape": events.ambiguity_shape(message) if found[0].get("reason") == "ORDER_ID_AMBIGUOUS" else "",
            "events": [{"order_id": e.get("order_id"), "reason": e.get("reason"), "status": e.get("status"), "has_total": e.get("grand_total") is not None} for e in found]}


def _status(notion, source_id, message, age_days=0):
    return _judge(notion, source_id, message["id"], _digest(message), age_days)


def _judge(notion, source_id, message_id, digest, age_days=0):
    """-> (why, detail). why: recorded, no_order_id (unreadable), no_row (order never recorded), not_listed (order recorded, this message not on it).
    A message that carries several orders is recorded only when EVERY one of its orders lists it.
    detail (fixed vocabulary only): the parser's reason code for an unreadable message, or the order row's Status for a not_listed one."""
    extracted_all = digest["events"]
    first = extracted_all[0]
    order_ids = [e.get("order_id") for e in extracted_all]
    if not all(order_ids):
        reason = first.get("reason") or "NONE"
        return "no_order_id", reason + (":" + digest["shape"] if reason == "ORDER_ID_AMBIGUOUS" else "")
    worst, aged = ("recorded", ""), False
    for order_id in order_ids:
        rows = _query_order(notion, source_id, order_id)
        if not rows:
            return "no_row", ""
        if len(rows) == 1 and message_id in set(x for x in (rows[0].get("Source Message IDs") or "").splitlines() if x):
            event = extracted_all[order_ids.index(order_id)]
            if event.get("status") == "ORDERED" and event.get("has_total") and rows[0].get("Grand Total") in (None, ""):
                worst = ("not_listed", "TOTAL_PENDING")                  # a confirmation the row has not taken its total from yet is kept, so it can still be read
            continue
        if len(rows) == 1 and rows[0].get("Status") == "REVIEW" and age_days > REVIEW_DAYS:
            aged = True                                                    # a conflicted order is never rewritten; its old mail may go
            continue
        worst = ("not_listed", str(rows[0].get("Status") or "NONE")[:30] if len(rows) == 1 else "DUPLICATE_ROWS")
    return ("recorded", "REVIEW_AGED") if worst[0] == "recorded" and aged else worst


def run(limit, live, environ=os.environ, gmail=None, notion=None, now=None, seen=None):
    """`seen`: the checkpoint, (load() -> {message id: digest}, save(digests) -> bool); by default the private store when this job has its credentials, else none (every mail is read, as before)."""
    counts = {"candidates": 0, "recorded": 0, "kept_unrecorded": 0, "no_order_id": 0, "no_row": 0, "not_listed": 0, "oldest_days": 0, "trashed": 0, "failed": 0}
    now = now or datetime.now(timezone.utc)
    if seen is None and (environ.get("LIFEOS_ACQ_DB_NAME") or "").strip():
        parser, stamp = parser_version(), now.replace(tzinfo=None)
        seen = (lambda: mail_checkpoint.load(READER, parser, stamp), lambda digests: mail_checkpoint.save(READER, parser, stamp, digests))
    reused = read = 0
    known, kept = (seen[0]() if seen else {}), {}
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
            digest = known.get(message_id)
            if digest is None:
                message = gmail.message_record(message_id)
                read += 1
                if "TRASH" in message["label_ids"]:
                    continue
                digest = _digest(message)
            else:
                reused += 1                                                                       # LIFE-906: the mail never changes; its saved digest is judged against Notion again, with no Gmail read
            kept[message_id] = digest
            age = (now - datetime.fromisoformat(digest["received_at"])).days
            counts["oldest_days"] = max(counts["oldest_days"], age)
            why, detail = _judge(notion, source_id, message_id, digest, age)
            if why != "recorded":
                counts["kept_unrecorded"] += 1
                counts[why] += 1
                tally(counts, f"{why}:{detail}", group="kept_detail")
                continue
            counts["recorded"] += 1
            if detail == "REVIEW_AGED":
                tally(counts, "review_aged")
            if not live:
                continue
            try:
                gmail.trash(message_id)
                counts["trashed"] += 1
                kept.pop(message_id, None)
            except GmailError:
                counts["failed"] += 1
        counts["gmail"] = {"listed": len(ids), "read": read, "reused": reused}
        if seen and not counts["failed"]:
            counts["checkpoint"] = "saved" if seen[1](kept) else "not_saved"                      # only a run that finished whole advances the checkpoint
    except (GmailError, AmazonError) as error:
        counts["failed"] += 1
        counts["error"] = str(error).split(" -> ", 1)[-1][:60]               # the HTTP code and reason, not the URL that used to fill the 60 characters and cut the code off
    return counts
