"""The ledger of mail that is new to the system, kept in the private database next to the Outlook tokens.

One row per message, keyed by the SHA-256 of `provider:id`; no id, address or content is stored. A moved message is
not new. Only a live, complete run writes (`commit`). Any read problem raises LedgerError and the stage falls back to
the full scan."""

import hashlib
from datetime import datetime, timedelta, timezone

KEEP_DAYS = 30
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS v7_mail_seen (
        key_hash CHAR(64) NOT NULL PRIMARY KEY, first_seen DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_mail_scan (
        name VARCHAR(16) NOT NULL PRIMARY KEY, at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)
NEW_MAIL, FULL_SCAN = "new mail", "14-day scan"


class LedgerError(RuntimeError):
    """Fixed code only."""


def key(provider, ident):
    return hashlib.sha256(f"{provider}:{ident}".encode()).hexdigest()


def _naive(moment):
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value else None


class Ledger:
    def __init__(self, connect):
        self._connect = connect

    def read(self):
        """-> (set of key hashes, time of the last read of any kind, kind of that read, time of the last 14-day
        scan)."""
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name IN ('v7_mail_seen','v7_mail_scan')"
                )
                if cursor.fetchone()[0] < 2:
                    return set(), None, None, None
                cursor.execute("SELECT key_hash FROM v7_mail_seen")
                keys = {row[0] for row in cursor.fetchall()}
                cursor.execute("SELECT name, at FROM v7_mail_scan")
                times = {row[0]: _aware(row[1]) for row in cursor.fetchall()}
        except Exception as error:  # noqa:
            raise LedgerError("MAIL_LEDGER_UNREADABLE") from error
        return keys, *last_read(times)

    def commit(self, keys, kind, now):
        """Record the messages this complete live run read, and when."""
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                for statement in SCHEMA:
                    cursor.execute(statement)
                stamp = _naive(now)
                cursor.executemany(
                    "INSERT IGNORE INTO v7_mail_seen (key_hash, first_seen) VALUES (%s, %s)",
                    [(k, stamp) for k in sorted(keys)],
                )
                for name in {"incremental", "full"} if kind == FULL_SCAN else {"incremental"}:
                    cursor.execute(
                        "INSERT INTO v7_mail_scan (name, at) VALUES (%s, %s) ON DUPLICATE KEY UPDATE at = VALUES(at)",
                        (name, stamp),
                    )
                cursor.execute(
                    "DELETE FROM v7_mail_seen WHERE first_seen < %s", (_naive(now - timedelta(days=KEEP_DAYS)),)
                )
        except Exception as error:  # noqa: BLE001
            raise LedgerError("MAIL_LEDGER_NOT_SAVED") from error


def production(environ):
    """The ledger over the private database, or None when this run has no database configured (the caller then does
    the full scan)."""
    if not (environ.get("LIFEOS_ACQ_DB_NAME") or "").strip():
        return None
    from lifeos.platform import db  # noqa:

    return Ledger(db.connect)


def last_read(times):
    """-> (time of the last read of any kind, its kind, time of the last 14-day scan) from {'incremental': t, 'full':
    t}."""
    incremental, full = times.get("incremental"), times.get("full")
    reads = [(t, kind) for t, kind in ((full, FULL_SCAN), (incremental, NEW_MAIL)) if t]
    if not reads:
        return None, None, full
    when, kind = max(reads, key=lambda pair: pair[0])
    return when, kind, full
