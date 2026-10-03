"""Job newsletters that arrive in an Outlook mailbox: the same senders, parsers and Jobs OS intake as Gmail, read through the Phase C
adapter (read-only: nothing in the mailbox is moved or marked). A message is recorded as seen only AFTER its cards are safely saved
(accounting before everything else); a parser gap (job links but no cards) stays unseen and is retried. Output is counts only.
Usage: python -m lifeos.run outlook-newsletters [--live]"""
import hashlib
import os
from datetime import datetime, timedelta, timezone

from lifeos.jobs import store as jobs_store
from lifeos.platform import outlook_tokens
from lifeos.platform.outlook import Outlook, OutlookError
from lifeos.sources.newsletters import config, ingest

SEEN_DDL = """CREATE TABLE IF NOT EXISTS v7_outlook_mail_seen (
    msg_key CHAR(40) NOT NULL PRIMARY KEY, account VARCHAR(32) NOT NULL, seen_at DATETIME NOT NULL, outcome VARCHAR(24) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
DEFAULT_ACCOUNTS = "personal"


def message_key(message_id):
    """A short stable key for an Outlook immutable id (they are far longer than the 40 characters the source table holds)."""
    return "o" + hashlib.sha256(message_id.encode()).hexdigest()[:39]


def _epoch(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def _seen(connection, keys):
    if not keys:
        return set()
    marks = ",".join(["%s"] * len(keys))
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT msg_key FROM v7_outlook_mail_seen WHERE msg_key IN ({marks})", tuple(keys))
        return {row[0] for row in cursor.fetchall()}


def _mark(connection, rows):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connection.cursor() as cursor:
        for key, account, outcome in rows:
            cursor.execute("INSERT IGNORE INTO v7_outlook_mail_seen (msg_key, account, seen_at, outcome) VALUES (%s,%s,%s,%s)",
                           (key, account, now, outcome))


def extract_account(client, account, connection, live, limit, now):
    counts = dict.fromkeys(("listed", "already_seen", "unsupported_sender", "messages", "cards", "new_jobs", "repeat_links",
                            "stale_mail", "non_job_mail", "no_cards", "marked_seen"), 0)
    since = (now - timedelta(days=ingest.MAX_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    listed = client.messages("inbox", since, limit=limit)
    counts["listed"] = len(listed)
    keys = {m["id"]: message_key(m["id"]) for m in listed}
    done_before = _seen(connection, list(keys.values()))
    marks = []
    for message in listed:
        key = keys[message["id"]]
        if key in done_before:
            counts["already_seen"] += 1
            continue
        sender = ((message.get("from") or {}).get("emailAddress") or {}).get("address") or ""
        rule = config.classify(sender)
        parser = ingest.PARSERS.get(rule)
        if parser is None:
            counts["unsupported_sender"] += 1
            continue
        counts["messages"] += 1
        received = _epoch(message["receivedDateTime"])
        if ingest.mail_age_days(received, now.timestamp()) > ingest.MAX_AGE_DAYS:
            counts["stale_mail"] += 1
            marks.append((key, account, "stale"))
            continue
        html = client.message_html(message["id"])
        cards = parser(html)
        counts["cards"] += len(cards)
        if not cards and not ingest.LOOKS_LIKE_JOBS[rule](html):
            counts["non_job_mail"] += 1
            marks.append((key, account, "non_job"))
            continue
        if not cards:
            counts["no_cards"] += 1                      # job links but no cards: a parser gap, retried next run
            continue
        if live:
            new, repeat = ingest.save_cards(connection, rule, key, cards, received)
            counts["new_jobs"] += new
            counts["repeat_links"] += repeat
            marks.append((key, account, "saved"))
    if live and marks:
        _mark(connection, marks)
        counts["marked_seen"] = len(marks)
    return counts


def run(limit, live, environ=os.environ, connect=None, outlook_factory=None, now=None):
    now = now or datetime.now(timezone.utc)
    client_id = (environ.get("OUTLOOK_CLIENT_ID") or "").strip()
    labels = [x.strip() for x in (environ.get("OUTLOOK_NEWSLETTER_ACCOUNTS") or DEFAULT_ACCOUNTS).split(",") if x.strip()]
    if not client_id or not labels:
        raise OutlookError("OUTLOOK_NEWSLETTER_CONFIG_MISSING")
    total = {"accounts": len(labels), "ok": 0, "failed": 0, "why": {}}
    with (connect or jobs_store.connect)() as connection:
        outlook_tokens.ensure_schema(connection)
        jobs_store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(SEEN_DDL)
        if live:
            ingest.backfill_provider(connection)
        for position, label in enumerate(labels, 1):
            token = outlook_tokens.load(connection, label)
            try:
                if not token:
                    raise OutlookError("OUTLOOK_NOT_SIGNED_IN")
                client = (outlook_factory or Outlook)(client_id, token,
                                                      lambda new, label=label: outlook_tokens.save(connection, label, new))
                counts = extract_account(client, label, connection, live, limit, now)
            except OutlookError as error:
                total["failed"] += 1
                code = f"{error}@{position}"             # position, never the label
                total["why"][code] = total["why"].get(code, 0) + 1
                continue
            total["ok"] += 1
            for key, value in counts.items():
                total[key] = total.get(key, 0) + value
    if total["failed"]:
        raise OutlookError(f"OUTLOOK_NEWSLETTERS_FAILED:{total['failed']}of{total['accounts']}:" + ",".join(sorted(total["why"])))
    return total
