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
    """Every column starts missing, each ALTER is refused with `error`, and a check made after a refused ALTER answers `landed` (did the other lane's column or key land?)."""
    def __init__(self, error, landed):
        self.error, self.landed, self.altered, self.last = error, landed, [], ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.last = sql
        if sql.startswith("ALTER"):
            self.altered.append(sql)
            raise self.error

    def fetchone(self):
        if "CHARACTER_MAXIMUM_LENGTH" in self.last:
            return (2000,)                                                      # the widened column is already wide
        return (1 if self.landed and self.altered else 0,)

    def fetchall(self):
        return []


class RacingConnection:
    def __init__(self, error, landed=True):
        self.error, self.landed = error, landed

    def cursor(self):
        return RacingCursor(self.error, self.landed)


def ensure(error, landed=True):
    with mock.patch.object(store, "existing_v7_tables", return_value=set()):
        return store.ensure_schema(RacingConnection(error, landed))


class SchemaRaceTests(unittest.TestCase):
    def test_a_column_or_key_another_lane_added_first_is_not_a_failure_once_it_is_confirmed_present(self):
        for code in (1060, 1061):
            ensure(Exception(code, "Duplicate"))                                    # no exception: the column and key are there either way

    def test_a_collision_that_leaves_the_column_or_key_missing_is_still_an_error(self):
        for code in (1060, 1061):
            with self.assertRaises(Exception) as caught:
                ensure(Exception(code, "Duplicate"), landed=False)
            self.assertEqual(caught.exception.args[0], code)

    def test_any_other_alter_failure_still_stops_the_stage(self):
        for error in (Exception(1142, "denied"), Exception(2013, "lost"), ValueError("x")):
            with self.assertRaises(type(error)):
                ensure(error)

    def test_every_index_clause_names_its_key_where_the_confirmation_reads_it(self):
        for _, _, _, index in store.COLUMNS:
            if index:
                self.assertRegex(index, r"^ADD KEY \w+ \(")


if __name__ == "__main__":
    unittest.main()
