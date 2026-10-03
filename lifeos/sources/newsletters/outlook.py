"""Job newsletters that arrive in an Outlook mailbox: shared parsers and Jobs OS intake, read through the Phase C adapter.
Like Gmail's sweep, mail from a known job-alert sender is moved out of the Inbox into a `J Newsletters` folder (needs the write sign-in;
nothing is ever deleted, human mail is never touched, every move is read back). A message is recorded as seen only AFTER its cards are
safely saved; a parser gap stays unseen and is retried. Reed course mail is filed without opening its body.
Without the write sign-in it still reads and ingests from the Inbox. Output is counts only.
Usage: python -m lifeos.run outlook-newsletters [--live]"""
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone

from lifeos.jobs import store as jobs_store
from lifeos.platform import outlook_tokens
from lifeos.platform.outlook import Outlook, OutlookError
from lifeos.sources.newsletters import config, ingest

SEEN_DDL = """CREATE TABLE IF NOT EXISTS v7_outlook_mail_seen (
    msg_key CHAR(40) NOT NULL PRIMARY KEY, account VARCHAR(32) NOT NULL, seen_at DATETIME NOT NULL, outcome VARCHAR(24) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
DEFAULT_ACCOUNTS = "personal,work"
FOLDER = "J Newsletters"
MAX_MOVES = 200                                      # per account per run: a safety cap, not a target
# Reed course mail is filed with newsletters but is not a job. Dice Private Email
# (user.dice.com and recruiter.dice.com) is human outreach and is deliberately absent.
SWEEP_ONLY = (("reed-course", re.compile(r"^[a-z0-9.-]+@courses\.reed\.co\.uk$")),)
OUTLOOK_ALERT_RULES = (("dice", re.compile(r"^dice@connect\.dice\.com$")),
                       ("reed", re.compile(r"^[a-z0-9.-]+@jobs\.reed\.co\.uk$")))


def sender_of(message):
    return (((message.get("from") or {}).get("emailAddress") or {}).get("address") or "").strip().lower()


def rule_of(sender):
    """(rule, parseable): a supported newsletter rule, or a file-only rule, else (None, False)."""
    rule = config.classify(sender)
    if rule:
        return rule, True
    for name, pattern in OUTLOOK_ALERT_RULES:
        if pattern.search(sender):
            return name, True
    for name, pattern in SWEEP_ONLY:
        if pattern.search(sender):
            return name, False
    return None, False


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


def sweep(client, live, limit, now):
    """File known job-alert senders from the Inbox into the J Newsletters folder. Returns (counts, folder id or None)."""
    counts = {"would_move": 0, "moved": 0, "move_failed": 0, "write_denied": 0}
    since = (now - timedelta(days=ingest.MAX_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    movable = [m for m in client.messages("inbox", since, limit=limit) if rule_of(sender_of(m))[0]]
    counts["would_move"] = len(movable)
    if not live:
        return counts, client.folder_id(FOLDER)
    if not movable:
        return counts, client.folder_id(FOLDER)
    try:
        folder = client.folder_id(FOLDER, create=True)
        for message in movable[:MAX_MOVES]:
            if client.move(message["id"], folder):
                counts["moved"] += 1
            else:
                counts["move_failed"] += 1
    except OutlookError as error:
        if str(error) not in ("OUTLOOK_HTTP_403", "OUTLOOK_UNAUTHORIZED"):
            raise
        counts["write_denied"] = 1                       # signed in read-only: keep reading from the Inbox, move nothing
        return counts, None
    return counts, folder


def extract_account(client, account, connection, live, limit, now):
    counts = dict.fromkeys(("listed", "already_seen", "unsupported_sender", "no_parser", "messages", "cards", "skipped", "new_jobs", "repeat_links",
                            "stale_mail", "non_job_mail", "no_cards", "marked_seen"), 0)
    moves, folder = sweep(client, live, limit, now)
    counts.update(moves)
    since = (now - timedelta(days=ingest.MAX_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    listed = {m["id"]: m for m in client.messages("inbox", since, limit=limit)}
    if folder:
        listed.update({m["id"]: m for m in client.messages(folder, since, limit=limit)})
    listed = list(listed.values())
    counts["listed"] = len(listed)
    keys = {m["id"]: message_key(m["id"]) for m in listed}
    done_before = _seen(connection, list(keys.values()))
    marks = []
    for message in listed:
        key = keys[message["id"]]
        if key in done_before:
            counts["already_seen"] += 1
            continue
        rule, parseable = rule_of(sender_of(message))
        if rule is None:
            counts["unsupported_sender"] += 1
            continue
        parser = ingest.PARSERS.get(rule) if parseable else None
        if parser is None:
            counts["no_parser"] += 1
            continue
        counts["messages"] += 1
        received = _epoch(message["receivedDateTime"])
        if ingest.mail_age_days(received, now.timestamp()) > ingest.MAX_AGE_DAYS:
            counts["stale_mail"] += 1
            marks.append((key, account, "stale"))
            continue
        html = client.message_html(message["id"])
        counted_parser = ingest.PARSERS_WITH_COUNTS.get(rule)
        if counted_parser:
            cards, skipped = counted_parser(html, received)
            counts["skipped"] += skipped
        else:
            cards = parser(html)
        counts["cards"] += len(cards)
        if not cards and rule != "reed" and not ingest.LOOKS_LIKE_JOBS[rule](html):
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
