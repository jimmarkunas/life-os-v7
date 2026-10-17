"""LIFE-64 live cause check: why a Newsletter message's saved vacancies are fewer than the cards parsed from it. Read-only, counts and fixed codes only.

Given ALERTS_MESSAGE_REF (the SHA-256 of one Gmail message id, never the id), it finds that message in Gmail by hashing the ids of the recent Newsletter mail, parses its cards with the
real parser, and for each card reads the Jobs store: is there a job row, does THIS message own a source row for it, do other messages own it, is it tombstoned (by its URL or only by its
opening: same company, title and location), and would a posting date newer than the retirement have let it back in. Each card gets one class: owned, collision (the job exists and only other
mail owns it: the unique-URL defect), orphan (a job nobody owns), suppressed_url / suppressed_opening (no job; an active tombstone), or missing (no job and no tombstone: unexplained).
Gmail is reached only through _ReadOnly (list ids, read one message) and the store only through a SELECT guard. No title, company, URL, sender or id is printed."""
import hashlib
import os
from datetime import datetime, timedelta, timezone

from lifeos.jobs import alertstate, identity, store
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.sources.newsletters import config, ingest

LOOKBACK = "label:j-newsletters newer_than:30d"
MAX_MAIL = 5000


class _ReadOnly:
    """The only Gmail calls this check can make. It has no label, send, move or delete method."""
    def __init__(self, gmail):
        self.list_ids, self.message = gmail.list_ids, gmail.message


def _one(cursor, sql, args):
    rows = alertstate._select(cursor, sql, args)
    return rows[0] if rows else None


def classify(cursor, message_id, card, received, now):
    key = identity.url_hash(card.url)
    job = _one(cursor, "SELECT id, status FROM v7_jobs WHERE dedupe_key = %s", (key,))
    mine = identity.url_hash(message_id + "|" + card.url)
    tombstone, relisted = "none", None
    if job:
        own = _one(cursor, "SELECT COUNT(*) FROM v7_job_sources WHERE job_id = %s AND gmail_message_id = %s", (job[0], message_id))[0] > 0
        others = _one(cursor, "SELECT COUNT(DISTINCT gmail_message_id) FROM v7_job_sources WHERE job_id = %s AND gmail_message_id <> %s", (job[0], message_id))[0]
        kind = "owned" if own else "collision" if others else "orphan"
    else:
        own = _one(cursor, "SELECT COUNT(*) FROM v7_job_sources WHERE job_id IS NULL AND gmail_message_id = %s AND source_url_hash = %s", (message_id, mine))[0] > 0
        others = 0
        hit = _one(cursor, "SELECT dedupe_key = %s, retired_at FROM v7_tombstones WHERE expires_at > %s AND (dedupe_key = %s OR fuzzy_key = %s) ORDER BY 1 DESC LIMIT 1",
                   (key, now, key, identity.fuzzy_key(card.company, card.title, card.location_text)))
        if hit:
            tombstone = "url" if hit[0] else "opening"
            posted = datetime.fromtimestamp(received, timezone.utc).date() - timedelta(days=card.age_days) if card.age_days is not None else None
            relisted = bool(posted and posted > hit[1].date())              # the date tombstone.blocked would accept as a genuinely newer vacancy
        kind = "suppressed_" + ("url" if tombstone == "url" else "opening") if hit else "missing"
    status = job[1] if job and job[1] in alertstate.KNOWN_STATUSES else ("other" if job else "none")
    return {"class": kind, "job": bool(job), "status": status, "own_row": own, "other_mails": int(others), "tombstone": tombstone, "relisted": relisted}


def trace(gmail, connection, message_ref, now=None):
    if not alertstate.MESSAGE_REF.fullmatch(message_ref):
        raise store.StoreError("NEWSLETTER_TRACE_MESSAGE_REF_INVALID")
    try:
        message_id = next((i for i in gmail.list_ids(LOOKBACK, limit=MAX_MAIL) if hashlib.sha256(i.encode()).hexdigest() == message_ref), None)
        if message_id is None:
            raise store.StoreError("NEWSLETTER_TRACE_MESSAGE_NOT_FOUND")
        sender, html, received = gmail.message(message_id)
    except GmailError:
        raise store.StoreError("NEWSLETTER_TRACE_GMAIL_FAILED") from None
    rule = config.classify(sender)
    parser = ingest.PARSERS.get(rule)
    if parser is None:
        raise store.StoreError("NEWSLETTER_TRACE_UNSUPPORTED_SENDER")
    cards = parser(html)
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with connection.cursor() as cursor:
            cards_out = [{"n": n, "source": rule, **classify(cursor, message_id, card, received, now)} for n, card in enumerate(cards, 1)]
            rows = _one(cursor, "SELECT COUNT(*), SUM(job_id IS NULL) FROM v7_job_sources WHERE gmail_message_id = %s", (message_id,))
    except store.StoreError:
        raise
    except Exception:
        raise store.StoreError("NEWSLETTER_TRACE_QUERY_FAILED") from None
    by_class = {}
    for card in cards_out:
        by_class[card["class"]] = by_class.get(card["class"], 0) + 1
    return {"observed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "card_trace": {"parsed": len(cards), "source_rows": int(rows[0]), "job_less_rows": int(rows[1] or 0), "by_class": dict(sorted(by_class.items())), "cards": cards_out}}


def run(limit, live):
    if live:
        raise store.StoreError("NEWSLETTER_TRACE_READ_ONLY")
    with store.connect() as connection:
        return trace(_ReadOnly(Gmail.from_env()), connection, (os.environ.get("ALERTS_MESSAGE_REF") or "").strip())
