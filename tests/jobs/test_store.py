import re
import unittest
from unittest import mock

from lifeos.jobs import store


class StoreSchemaTests(unittest.TestCase):
    def test_only_v7_tables_are_ever_created(self):
        created = [re.search(r"EXISTS (\w+)", s).group(1) for s in store.SCHEMA]
        self.assertEqual(created, list(store.TABLES))
        self.assertTrue(all(name.startswith("v7_") for name in created))

    def test_schema_is_idempotent_ddl(self):
        self.assertTrue(all(s.lstrip().startswith("CREATE TABLE IF NOT EXISTS") for s in store.SCHEMA))


class RacingCursor:
    """A cursor that reports every column missing, then refuses each ALTER as another lane having just added it."""
    def __init__(self, code):
        self.code, self.altered, self.last = code, [], ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.last = sql
        if sql.startswith("ALTER"):
            self.altered.append(sql)
            raise self.code if isinstance(self.code, Exception) else Exception(self.code, "x")

    def fetchone(self):
        return (2000,) if "CHARACTER_MAXIMUM_LENGTH" in self.last else (0,)        # every column missing, the widened one already wide

    def fetchall(self):
        return []


class RacingConnection:
    def __init__(self, error):
        self.error = error

    def cursor(self):
        return RacingCursor(self.error)


class SchemaRaceTests(unittest.TestCase):
    def test_a_column_or_key_another_lane_added_first_is_not_a_failure(self):
        for code in (1060, 1061):
            connection = RacingConnection(Exception(code, "Duplicate"))
            with mock.patch.object(store, "existing_v7_tables", return_value=set()):
                store.ensure_schema(connection)                                      # no exception: the column is there either way

    def test_any_other_alter_failure_still_stops_the_stage(self):
        for error in (Exception(1142, "denied"), Exception(2013, "lost"), ValueError("x")):
            with mock.patch.object(store, "existing_v7_tables", return_value=set()), self.assertRaises(type(error)):
                store.ensure_schema(RacingConnection(error))


if __name__ == "__main__":
    unittest.main()
