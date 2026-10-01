import re
import unittest

from lifeos.jobs import store


class StoreSchemaTests(unittest.TestCase):
    def test_only_v7_tables_are_ever_created(self):
        created = [re.search(r"EXISTS (\w+)", s).group(1) for s in store.SCHEMA]
        self.assertEqual(created, list(store.TABLES))
        self.assertTrue(all(name.startswith("v7_") for name in created))

    def test_schema_is_idempotent_ddl(self):
        self.assertTrue(all(s.lstrip().startswith("CREATE TABLE IF NOT EXISTS") for s in store.SCHEMA))


if __name__ == "__main__":
    unittest.main()
