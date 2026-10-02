"""Private storage for the Jira snapshot: one row per project, replaced each run (the database is private; this repo is not)."""
import json
from datetime import datetime

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS v7_jira_snapshot (
        project_key VARCHAR(16) NOT NULL PRIMARY KEY,
        taken_at DATETIME NOT NULL, schema_v SMALLINT NOT NULL, payload MEDIUMTEXT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


def ensure_schema(connection):
    with connection.cursor() as cursor:
        for statement in SCHEMA:
            cursor.execute(statement)


def save(connection, snapshot):
    taken = datetime.fromisoformat(snapshot["taken_at"]).replace(tzinfo=None)
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO v7_jira_snapshot (project_key, taken_at, schema_v, payload) VALUES (%s,%s,%s,%s) "
                       "ON DUPLICATE KEY UPDATE taken_at=VALUES(taken_at), schema_v=VALUES(schema_v), payload=VALUES(payload)",
                       (snapshot["project"], taken, snapshot["schema"], json.dumps(snapshot, ensure_ascii=False)))


def load(connection, project):
    with connection.cursor() as cursor:
        cursor.execute("SELECT payload FROM v7_jira_snapshot WHERE project_key=%s", (project,))
        row = cursor.fetchone()
    return json.loads(row[0]) if row else None
