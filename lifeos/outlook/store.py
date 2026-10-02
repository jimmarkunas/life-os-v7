"""Private storage for Outlook refresh tokens: one row per account label, replaced whenever Microsoft rotates the token.
The database is private; this repo and its logs are not (labels like 'personal' are not addresses)."""
from datetime import datetime, timezone

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS v7_outlook_token (
        account VARCHAR(32) NOT NULL PRIMARY KEY,
        refresh_token TEXT NOT NULL, updated_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


def ensure_schema(connection):
    with connection.cursor() as cursor:
        for statement in SCHEMA:
            cursor.execute(statement)


def save(connection, account, refresh_token):
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO v7_outlook_token (account, refresh_token, updated_at) VALUES (%s,%s,%s) "
                       "ON DUPLICATE KEY UPDATE refresh_token=VALUES(refresh_token), updated_at=VALUES(updated_at)",
                       (account, refresh_token, datetime.now(timezone.utc).replace(tzinfo=None)))


def load(connection, account):
    with connection.cursor() as cursor:
        cursor.execute("SELECT refresh_token FROM v7_outlook_token WHERE account=%s", (account,))
        row = cursor.fetchone()
    return row[0] if row else None


def accounts(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT account FROM v7_outlook_token ORDER BY account")
        return [row[0] for row in cursor.fetchall()]
