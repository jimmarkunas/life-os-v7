"""One private snapshot table: a row per key (a project, or the single row 1), replaced each run. The database is private; this repo is not.
Used by the Jira, Bills and Agenda snapshots, so the schema, the replace and the read-back live in one place."""
import json
from datetime import datetime


class Store:
    def __init__(self, table, key_column="snapshot_id", key_type="TINYINT"):
        self.table, self.key_column = table, key_column
        self.schema = (f"CREATE TABLE IF NOT EXISTS {table} ({key_column} {key_type} NOT NULL PRIMARY KEY, "
                       "taken_at DATETIME NOT NULL, schema_v SMALLINT NOT NULL, payload MEDIUMTEXT NOT NULL"
                       ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",)

    def ensure(self, connection):
        with connection.cursor() as cursor:
            for statement in self.schema:
                cursor.execute(statement)

    def save(self, connection, key, snapshot):
        taken = datetime.fromisoformat(snapshot["taken_at"]).replace(tzinfo=None)
        with connection.cursor() as cursor:
            cursor.execute(f"INSERT INTO {self.table} ({self.key_column}, taken_at, schema_v, payload) VALUES (%s,%s,%s,%s) "
                           "ON DUPLICATE KEY UPDATE taken_at=VALUES(taken_at), schema_v=VALUES(schema_v), payload=VALUES(payload)",
                           (key, taken, snapshot["schema"], json.dumps(snapshot, ensure_ascii=False)))

    def load(self, connection, key):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT payload FROM {self.table} WHERE {self.key_column}=%s", (key,))
            row = cursor.fetchone()
        return json.loads(row[0]) if row else None

    def save_verified(self, connection, key, snapshot, fail):
        """Ensure the table, replace the row and read it back. `fail(code)` builds the caller's own error for a mismatch."""
        self.ensure(connection)
        self.save(connection, key, snapshot)
        if self.load(connection, key) != snapshot:
            raise fail("READBACK_MISMATCH")
