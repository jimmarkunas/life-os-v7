"""Stage 3 - extract: parse job cards out of labeled newsletters into v7_jobs (status NEW).

Only supported senders are processed; others stay pending. A message is marked Processed only AFTER its
cards are safely written (accounting before cleanup). Dry run parses and counts; it writes nothing.
Usage: python -m pipeline.extract [--live] [--limit N]      Logs: counts only (public repo).
"""
import argparse
from datetime import datetime, timezone
import time
from hashlib import sha256
import re
import sys

from pipeline import config, store
from pipeline.gmail import Gmail, GmailError
from pipeline.parsers import lensa

PARSERS = {"lensa": lensa.parse}
MAX_AGE_DAYS = 14   # known-older jobs are kept for dedupe but never published


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def fuzzy_key(card):
    return sha256("|".join(_norm(x) for x in (card.company, card.title, card.location_text)).encode()).hexdigest()


def url_hash(url):
    return sha256(url.encode()).hexdigest()


def mail_age_days(received_epoch, now=None):
    return max(0, int(((now or time.time()) - received_epoch) // 86400))


def known_age(card, mail_age):
    """Lower bound on the job's age today: its own 'posted N ago' (if any) plus how old the email already is."""
    return (card.age_days or 0) + mail_age


def status_for(card, mail_age=0):
    return "EXCLUDED_STALE" if known_age(card, mail_age) > MAX_AGE_DAYS else "NEW"


def save_cards(connection, rule, message_id, cards, received_epoch=None):
    """Insert cards idempotently. Returns (new_jobs, repeat_links)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    mail_age = mail_age_days(received_epoch) if received_epoch else 0
    received = datetime.fromtimestamp(received_epoch, timezone.utc).replace(tzinfo=None) if received_epoch else None
    new = repeat = 0
    with connection.cursor() as cursor:
        for card in cards:
            key = url_hash(card.url)
            cursor.execute(
                "INSERT IGNORE INTO v7_jobs (dedupe_key, status, title, company, location_text, source_url, salary_text,"
                " source, fuzzy_key, posted_age_days, mail_received_at, first_seen, last_seen, updated_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (key, status_for(card, mail_age), card.title[:300], card.company[:200], (card.location_text or "")[:200], card.url,
                 (card.salary_text or "")[:80], rule, fuzzy_key(card), known_age(card, mail_age), received, now, now, now))
            new += cursor.rowcount
            repeat += 0 if cursor.rowcount else 1
            cursor.execute(
                "INSERT IGNORE INTO v7_job_sources (job_id, source, source_url_hash, gmail_message_id, seen_at)"
                " SELECT id, %s, %s, %s, %s FROM v7_jobs WHERE dedupe_key = %s", (rule, key, message_id, now, key))
    return new, repeat


def extract(gmail, live, limit, connection=None):
    processed_label = gmail.label_id(config.PROCESSED_LABEL, create=live)
    counts = {"messages": 0, "cards": 0, "new_jobs": 0, "repeat_links": 0,
              "unsupported_sender": 0, "no_cards": 0, "stale_mail": 0, "marked_processed": 0}
    done = []
    for message_id in gmail.list_ids(config.PENDING_QUERY, limit=limit):
        sender, html, received = gmail.message(message_id)
        rule = config.classify(sender)
        parser = PARSERS.get(rule)
        if parser is None:
            counts["unsupported_sender"] += 1
            continue
        counts["messages"] += 1
        if mail_age_days(received) > MAX_AGE_DAYS:   # every job in it is already older than the window
            counts["stale_mail"] += 1
            if live:
                done.append(message_id)
            continue
        cards = parser(html)
        counts["cards"] += len(cards)
        counts["no_cards"] += 0 if cards else 1
        if live and cards:                      # zero cards = parser gap: leave pending, never mark Processed
            new, repeat = save_cards(connection, rule, message_id, cards, received)
            counts["new_jobs"] += new
            counts["repeat_links"] += repeat
            done.append(message_id)
    if live and done:
        gmail.relabel(done, add=[processed_label])
        counts["marked_processed"] = len(done)
    return counts


def backfill_dates(gmail, connection):
    """Rows saved before mail dates were tracked: fill the date and apply the 14-day rule. Runs once per message."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT DISTINCT s.gmail_message_id FROM v7_job_sources s JOIN v7_jobs j ON j.id = s.job_id"
                       " WHERE j.mail_received_at IS NULL")
        ids = [row[0] for row in cursor.fetchall()]
    done = stale = 0
    for message_id in ids:
        _, _, received = gmail.message(message_id)
        age = mail_age_days(received)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE v7_jobs j JOIN v7_job_sources s ON s.job_id = j.id SET"
                " j.mail_received_at = %s, j.posted_age_days = COALESCE(j.posted_age_days, 0) + %s,"
                " j.status = IF(j.status = 'NEW' AND COALESCE(j.posted_age_days, 0) + %s > %s, 'EXCLUDED_STALE', j.status)"
                " WHERE s.gmail_message_id = %s AND j.mail_received_at IS NULL",
                (datetime.fromtimestamp(received, timezone.utc).replace(tzinfo=None), age, age, MAX_AGE_DAYS, message_id))
            stale += cursor.rowcount
        done += 1
    return {"backfilled_messages": done, "rows_dated": stale}


def reconcile(gmail, connection, limit):
    """Repair: Processed mail that has no rows in v7_job_sources (e.g. marked before a parser fix). Idempotent."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT DISTINCT gmail_message_id FROM v7_job_sources")
        known = {row[0] for row in cursor.fetchall()}
    counts = {"checked": 0, "recovered_messages": 0, "recovered_jobs": 0, "still_no_cards": 0,
              "stale_mail": 0, "backfilled_messages": 0, "rows_dated": 0}
    counts.update(backfill_dates(gmail, connection))
    for message_id in gmail.list_ids(config.RECONCILE_QUERY, limit=limit):
        if message_id in known:
            continue
        sender, html, received = gmail.message(message_id)
        rule = config.classify(sender)
        parser = PARSERS.get(rule)
        if parser is None:
            continue
        counts["checked"] += 1
        if mail_age_days(received) > MAX_AGE_DAYS:
            counts["stale_mail"] += 1
            continue
        cards = parser(html)
        if not cards:
            counts["still_no_cards"] += 1
            continue
        new, _ = save_cards(connection, rule, message_id, cards, received)
        counts["recovered_messages"] += 1
        counts["recovered_jobs"] += new
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--reconcile", action="store_true", help="repair pass over recent Processed mail (needs --live)")
    args = parser.parse_args(argv)
    try:
        gmail = Gmail.from_env()
        if args.reconcile:
            if not args.live:
                print("reconcile needs --live (it reads the store)", file=sys.stderr)
                return 1
            with store.connect() as connection:
                store.ensure_schema(connection)
                print("reconcile LIVE:", reconcile(gmail, connection, args.limit))
            return 0
        if args.live:
            with store.connect() as connection:
                store.ensure_schema(connection)
                counts = extract(gmail, True, args.limit, connection)
        else:
            counts = extract(gmail, False, args.limit)
    except (GmailError, store.StoreError) as error:
        print(f"EXTRACT FAILED: {error}", file=sys.stderr)
        return 1
    print(f"extract {'LIVE' if args.live else 'DRY-RUN'}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
