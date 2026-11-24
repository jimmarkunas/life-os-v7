"""LIFE-906: what a read of one old mail concluded, kept in the private store so the next hourly run does not read that mail from Gmail again.

Amazon retention asks Notion about the same old mail every hour; the mail itself never changes. One row per (reader, Gmail message id) holds a small digest (order numbers, statuses, whether a total was seen), the parser
version that made it and the time. A digest is used only while it is fresh (REUSE_HOURS) and its parser version is the current one; otherwise that mail is read again. A missing table, an unreadable store or a store
error all mean `read everything`; nothing is saved from a run that did not finish. Never raises. Counts and fixed codes only."""
from datetime import timedelta
import json

from lifeos.platform import db

TABLE = "v7_mail_checkpoint"
CREATE = ("CREATE TABLE IF NOT EXISTS v7_mail_checkpoint ("
          " reader VARCHAR(24) NOT NULL, message_id VARCHAR(40) NOT NULL, parser CHAR(16) NOT NULL, digest TEXT NOT NULL, seen_at DATETIME NOT NULL,"
          " PRIMARY KEY (reader, message_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
REUSE_HOURS = 24                          # a digest older than this is not trusted: that mail is read again, so every message is read at least daily
KEEP_DAYS = 7


def load(reader, parser, now, connect=None):
    """-> {message id: digest} for this reader's fresh rows made by this parser version, else {} (full read)."""
    try:
        with (connect or db.connect)() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT message_id, digest FROM {TABLE} WHERE reader=%s AND parser=%s AND seen_at >= %s", (reader, parser, now - timedelta(hours=REUSE_HOURS)))
            return {message_id: json.loads(digest) for message_id, digest in cursor.fetchall()}
    except Exception:                                                              # noqa: BLE001 - any problem: read the whole window
        return {}


def save(reader, parser, now, digests, connect=None):
    """File `digests` ({message id: digest}) and forget this reader's rows that are not in them or are older than KEEP_DAYS. -> True when saved; False on any problem (the next run reads in full)."""
    try:
        with (connect or db.connect)() as connection, connection.cursor() as cursor:
            cursor.execute(CREATE)
            for message_id, digest in digests.items():
                cursor.execute(f"REPLACE INTO {TABLE} (reader, message_id, parser, digest, seen_at) VALUES (%s,%s,%s,%s,%s)", (reader, message_id, parser, json.dumps(digest, separators=(",", ":")), now))
            cursor.execute(f"DELETE FROM {TABLE} WHERE reader=%s AND seen_at < %s", (reader, now - timedelta(days=KEEP_DAYS)))
            if digests:
                cursor.execute(f"DELETE FROM {TABLE} WHERE reader=%s AND message_id NOT IN ({','.join(['%s'] * len(digests))})", (reader, *digests))
            else:
                cursor.execute(f"DELETE FROM {TABLE} WHERE reader=%s", (reader,))
            connection.commit()
        return True
    except Exception:                                                              # noqa: BLE001
        return False
