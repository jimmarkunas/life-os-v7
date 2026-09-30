"""Stage 3 - extract: parse job cards out of labeled newsletters into v7_jobs (status NEW).

Only supported senders are processed; others stay pending. A message is marked Processed only AFTER its
cards are safely written (accounting before cleanup). Dry run parses and counts; it writes nothing.
Usage: python -m pipeline.extract [--live] [--limit N]      Logs: counts only (public repo).
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import re
import sys

from pipeline import config, store
from pipeline.gmail import Gmail, GmailError
from pipeline.parsers import lensa

PARSERS = {"lensa": lensa.parse}


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def fuzzy_key(card):
    return sha256("|".join(_norm(x) for x in (card.company, card.title, card.location_text)).encode()).hexdigest()


def url_hash(url):
    return sha256(url.encode()).hexdigest()


def save_cards(connection, rule, message_id, cards):
    """Insert cards idempotently. Returns (new_jobs, repeat_links)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    new = repeat = 0
    with connection.cursor() as cursor:
        for card in cards:
            key = url_hash(card.url)
            cursor.execute(
                "INSERT IGNORE INTO v7_jobs (dedupe_key, status, title, company, location_text, source_url, salary_text,"
                " source, fuzzy_key, first_seen, last_seen, updated_at) VALUES (%s,'NEW',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (key, card.title[:300], card.company[:200], (card.location_text or "")[:200], card.url,
                 (card.salary_text or "")[:80], rule, fuzzy_key(card), now, now, now))
            new += cursor.rowcount
            repeat += 0 if cursor.rowcount else 1
            cursor.execute(
                "INSERT IGNORE INTO v7_job_sources (job_id, source, source_url_hash, gmail_message_id, seen_at)"
                " SELECT id, %s, %s, %s, %s FROM v7_jobs WHERE dedupe_key = %s", (rule, key, message_id, now, key))
    return new, repeat


def extract(gmail, live, limit, connection=None):
    processed_label = gmail.label_id(config.PROCESSED_LABEL, create=live)
    counts = {"messages": 0, "cards": 0, "new_jobs": 0, "repeat_links": 0,
              "unsupported_sender": 0, "no_cards": 0, "marked_processed": 0}
    done = []
    for message_id in gmail.list_ids(config.PENDING_QUERY, limit=limit):
        sender, html = gmail.message(message_id)
        rule = config.classify(sender)
        parser = PARSERS.get(rule)
        if parser is None:
            counts["unsupported_sender"] += 1
            continue
        counts["messages"] += 1
        cards = parser(html)
        counts["cards"] += len(cards)
        counts["no_cards"] += 0 if cards else 1
        if live:
            new, repeat = save_cards(connection, rule, message_id, cards)
            counts["new_jobs"] += new
            counts["repeat_links"] += repeat
            done.append(message_id)
    if live and done:
        gmail.relabel(done, add=[processed_label])
        counts["marked_processed"] = len(done)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        gmail = Gmail.from_env()
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
