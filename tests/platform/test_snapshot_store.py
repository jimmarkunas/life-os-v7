import json
import unittest

from lifeos.platform.snapshot_store import Store
from tests.kit.db import FakeConn


def snap(**extra):
    return {"schema": 1, "taken_at": "2026-10-03T08:00:00-05:00", **extra}


class Rows:
    """In-memory table for the generic snapshot SQL: key -> payload."""

    def __init__(self):
        self.rows, self.sql = {}, []

    def handler(self, sql, args, cursor):
        self.sql.append(sql)
        if sql.startswith("INSERT INTO"):
            self.rows[args[0]] = args[-1]
        elif sql.startswith("SELECT payload"):
            cursor.row = (self.rows[args[0]],) if args[0] in self.rows else None


class SnapshotStoreTests(unittest.TestCase):
    def test_schema_names_the_table_and_key_column(self):
        text = Store("v7_x_snapshot", "project_key", "VARCHAR(16)").schema[0]
        self.assertIn("v7_x_snapshot", text)
        self.assertIn("project_key VARCHAR(16) NOT NULL PRIMARY KEY", text)
        self.assertIn("snapshot_id TINYINT", Store("v7_y_snapshot").schema[0])

    def test_save_replaces_and_load_returns_the_payload_per_key(self):
        rows = Rows()
        conn, store = FakeConn(handler=rows.handler), Store("v7_x_snapshot")
        store.save(conn, 1, snap(n=1))
        store.save(conn, 1, snap(n=2))
        store.save(conn, 2, snap(n=3))
        self.assertEqual((store.load(conn, 1)["n"], store.load(conn, 2)["n"], store.load(conn, 9)), (2, 3, None))
        self.assertEqual(json.loads(rows.rows[1])["n"], 2)

    def test_save_verified_raises_the_callers_own_error_on_a_mismatch(self):
        class Lossy(Rows):
            def handler(self, sql, args, cursor):
                super().handler(sql, args, cursor)
                if sql.startswith("INSERT INTO"):
                    self.rows[args[0]] = json.dumps({"schema": 1, "taken_at": "x"})       # what the database "stored" differs

        conn = FakeConn(handler=Lossy().handler)
        with self.assertRaises(RuntimeError) as error:
            Store("v7_x_snapshot").save_verified(conn, 1, snap(n=1), lambda code: RuntimeError("X_" + code))
        self.assertEqual(str(error.exception), "X_READBACK_MISMATCH")


if __name__ == "__main__":
    unittest.main()
