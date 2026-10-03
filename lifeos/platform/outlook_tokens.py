"""Private storage for Outlook refresh tokens: one row per account label, replaced whenever Microsoft rotates the token.
The database is private; this repo and its logs are not (labels like 'personal' are not addresses)."""
from datetime import datetime, timezone

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS v7_outlook_token (
        account VARCHAR(32) NOT NULL PRIMARY KEY,
        refresh_token TEXT NOT NULL, updated_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)
COLUMNS = (("fingerprint", "ALTER TABLE v7_outlook_token ADD COLUMN fingerprint VARCHAR(64) NULL"),)


def ensure_schema(connection):
    with connection.cursor() as cursor:
        for statement in SCHEMA:
            cursor.execute(statement)
        for column, alter in COLUMNS:                               # works on MySQL and MariaDB alike: ask first, add if missing
            cursor.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() "
                           "AND table_name = 'v7_outlook_token' AND column_name = %s", (column,))
            if not cursor.fetchone()[0]:
                cursor.execute(alter)


def save(connection, account, refresh_token, fingerprint=None):
    """Insert or replace. A rotated token (no fingerprint given) keeps the stored fingerprint."""
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO v7_outlook_token (account, refresh_token, updated_at, fingerprint) VALUES (%s,%s,%s,%s) "
                       "ON DUPLICATE KEY UPDATE refresh_token=VALUES(refresh_token), updated_at=VALUES(updated_at), "
                       "fingerprint=COALESCE(VALUES(fingerprint), fingerprint)",
                       (account, refresh_token, datetime.now(timezone.utc).replace(tzinfo=None), fingerprint))


def fingerprint_of(connection, account):
    with connection.cursor() as cursor:
        cursor.execute("SELECT fingerprint FROM v7_outlook_token WHERE account=%s", (account,))
        row = cursor.fetchone()
    return row[0] if row else None


def label_with(connection, fingerprint):
    with connection.cursor() as cursor:
        cursor.execute("SELECT account FROM v7_outlook_token WHERE fingerprint=%s", (fingerprint,))
        row = cursor.fetchone()
    return row[0] if row else None


def load(connection, account):
    with connection.cursor() as cursor:
        cursor.execute("SELECT refresh_token FROM v7_outlook_token WHERE account=%s", (account,))
        row = cursor.fetchone()
    return row[0] if row else None


def accounts(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT account FROM v7_outlook_token ORDER BY account")
        return [row[0] for row in cursor.fetchall()]
