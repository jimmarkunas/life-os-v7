"""(synthetic): the DDL proposal is the ledger's own schema, and the read-only check reports flags only."""
import unittest

from lifeos.inbox import apply_schema, ledger, schema


class Cursor:
    """Answers the five statements the check runs, from a canned server."""

    def __init__(self, version="8.0.36", row_format="dynamic", tables=(), grants=("GRANT SELECT, INSERT, UPDATE, CREATE ON `x`.* TO `u`@`%`",), refuse=()):
        self.version, self.row_format, self.tables, self.grants, self.refuse, self.sql, self.rows = version, row_format, tables, grants, refuse, [], []

    def execute(self, sql):
        self.sql.append(sql)
        if any(word in sql for word in self.refuse):
            raise OSError("refused")
        if "VERSION" in sql:
            self.rows = [(self.version,)]
        elif "row_format" in sql:
            self.rows = [(self.row_format,)]
        elif sql.startswith("SHOW TABLES"):
            self.rows = [(t,) for t in self.tables]
        elif sql.startswith("SHOW GRANTS"):
            self.rows = [(g,) for g in self.grants]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Server(Cursor):
    """A canned server that also takes the CREATE statements and answers SHOW COLUMNS and COUNT for the tables it holds.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.made = list(self.tables)

    def execute(self, sql):
        if sql.startswith("CREATE TABLE IF NOT EXISTS"):
            self.sql.append(sql)
            self.made.append(sql.split("EXISTS ")[1].split(" ")[0])
            self.tables = tuple(self.made)
            return
        if sql.startswith("SHOW COLUMNS FROM"):
            self.sql.append(sql)
            self.rows = [("c",)] * schema.COLUMNS[sql.split()[-1]]
            return
        if sql.startswith("SELECT COUNT(*)"):
            self.sql.append(sql)
            self.rows = [(0,)]
            return
        super().execute(sql)


class Apply(unittest.TestCase):
    def test_apply_creates_the_four_tables_and_reads_them_back(self):
        server = Server()
        result = apply_schema.apply(server)
        self.assertEqual((result["created"], result["verified"], result["rows"]), (sorted(schema.TABLES), True, {t: 0 for t in schema.TABLES}))
        self.assertEqual(result["columns"], schema.COLUMNS)
        self.assertEqual([s for s in server.sql if s.startswith("CREATE")], schema.plan())  # exactly the planned DDL, nothing else is changed
        self.assertFalse([s for s in server.sql if s.split()[0] in ("DROP", "ALTER", "DELETE", "UPDATE", "INSERT", "TRUNCATE")])

    def test_apply_refuses_a_server_that_is_not_ready_or_already_has_a_table(self):
        with self.assertRaises(apply_schema.SchemaError) as caught:
            apply_schema.apply(Server(row_format="compact"))
        self.assertEqual(str(caught.exception), "INBOX_SCHEMA_NOT_READY")
        with self.assertRaises(apply_schema.SchemaError) as caught:
            apply_schema.apply(Server(tables=("v7_inbox_events",)))
        self.assertEqual(str(caught.exception), "INBOX_SCHEMA_TABLES_EXIST")

    def test_a_dry_run_through_the_stage_creates_nothing(self):
        server = Server()
        from unittest import mock
        connection = mock.MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value = server
        with mock.patch.object(apply_schema.db, "connect", return_value=connection), mock.patch.dict("os.environ", {"SCHEMA_CONFIRM": ""}):
            result = apply_schema.apply_stage(0, True)
        self.assertEqual((result["dry_run"], result["check"]["ready"]), (True, True))
        self.assertFalse([s for s in server.sql if s.startswith("CREATE")])
        with mock.patch.object(apply_schema.db, "connect", return_value=connection), mock.patch.dict("os.environ", {"SCHEMA_CONFIRM": "APPLY"}):
            applied = apply_schema.apply_stage(0, False)
        self.assertTrue(applied["verified"])


class Schema(unittest.TestCase):
    def test_the_plan_is_exactly_the_ledgers_schema_for_the_four_tables(self):
        self.assertEqual(schema.plan(), list(ledger.SCHEMA))
        names = [s.split("EXISTS ")[1].split(" ")[0] for s in schema.plan()]
        self.assertEqual(names, list(schema.TABLES))

    def test_a_ready_server_reports_true_flags_and_nothing_else(self):
        result = schema.check(Cursor())
        self.assertEqual((result["server"], result["version"], result["row_format"], result["tables_present"], result["ready"]), ("mysql", "8.0", "dynamic", [], True))
        self.assertEqual(result["grants"], {"CREATE": True, "SELECT": True, "INSERT": True, "UPDATE": True})
        self.assertNotIn("x", repr(result))

    def test_an_old_row_format_a_missing_grant_or_an_existing_table_is_visible(self):
        compact = schema.check(Cursor(row_format="compact"))
        self.assertFalse(compact["ready"])
        no_create = schema.check(Cursor(grants=("GRANT SELECT, INSERT, UPDATE ON `x`.* TO `u`@`%`",)))
        self.assertEqual((no_create["grants"]["CREATE"], no_create["ready"]), (False, False))
        present = schema.check(Cursor(tables=("v7_inbox_events", "v7_other")))
        self.assertEqual(present["tables_present"], ["v7_inbox_events"])  # only this schema's own table names are reported
        everything = schema.check(Cursor(grants=("GRANT ALL PRIVILEGES ON `x`.* TO `u`@`%`",)))
        self.assertTrue(everything["ready"])

    def test_a_refused_statement_is_a_none_never_an_error_text(self):
        result = schema.check(Cursor(refuse=("row_format", "GRANTS")))
        self.assertEqual((result["row_format"], result["grants"], result["ready"]), (None, None, False))
        self.assertNotIn("refused", repr(result))

    def test_the_check_only_reads(self):
        cursor = Cursor()
        schema.check(cursor)
        self.assertTrue(all(s.split()[0] in ("SELECT", "SHOW") for s in cursor.sql), cursor.sql)


if __name__ == "__main__":
    unittest.main()
