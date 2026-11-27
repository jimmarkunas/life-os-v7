"""(synthetic): the Followup tables' check and one-time apply: flags only, refuses unless ready, creates only the planned
DDL and reads it back.
"""
import json
import pathlib
import unittest
from unittest import mock

from lifeos import run
from lifeos.inbox import followup_apply as fa, followup_schema as fs, followup_store

READY_GRANT = "GRANT SELECT, INSERT, UPDATE, CREATE ON `x`.* TO `u`@`%`"


class Cursor:
    """Answers the five statements the check runs, from a canned server."""

    def __init__(self, version="8.0.36", row_format="dynamic", tables=(), grants=(READY_GRANT,), refuse=()):
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

    def __init__(self, columns=None, count=0, **kw):
        super().__init__(**kw)
        self.made, self.columns, self.count = list(self.tables), columns or fs.column_counts(), count

    def execute(self, sql):
        if sql.startswith("CREATE TABLE IF NOT EXISTS"):
            self.sql.append(sql)
            self.made.append(sql.split("EXISTS ")[1].split(" ")[0])
            self.tables = tuple(self.made)
            return
        if sql.startswith("SHOW COLUMNS FROM"):
            self.sql.append(sql)
            self.rows = [("c",)] * self.columns[sql.split()[-1]]
            return
        if sql.startswith("SELECT COUNT(*)"):
            self.sql.append(sql)
            self.rows = [(self.count,)]
            return
        super().execute(sql)


def stage_cursor(server):
    connection = mock.MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = server
    return connection


class Plan(unittest.TestCase):
    def test_the_plan_is_exactly_the_stores_schema_for_the_five_followup_tables(self):
        self.assertEqual(fs.plan(), list(followup_store.SCHEMA))
        self.assertEqual([s.split("EXISTS ")[1].split(" ")[0] for s in fs.plan()], list(followup_store.TABLES))
        self.assertTrue(all(t.startswith("v7_followup_") for t in fs.TABLES))

    def test_column_counts_and_key_sizes_come_from_the_ddl(self):
        self.assertEqual(fs.column_counts(), {"v7_followup_threads": 16, "v7_followup_checkpoints": 5, "v7_followup_overrides": 4, "v7_followup_suppressions": 4, "v7_followup_payload": 4})
        self.assertEqual(fs.column_counts()["v7_followup_threads"], len(followup_store.THREAD_COLUMNS))
        self.assertEqual(fs.key_bytes(), {"v7_followup_threads": 444, "v7_followup_checkpoints": 468, "v7_followup_overrides": 256, "v7_followup_suppressions": 512, "v7_followup_payload": 762})
        self.assertLessEqual(max(fs.key_bytes().values()), fs.KEY_LIMITS["compact"])

    def test_the_stages_and_the_fixed_codes_are_registered(self):
        self.assertIn("followup-schema-check", run.STAGES)
        self.assertIn("followup-schema-apply", run.STAGES)
        self.assertEqual((run.STAGES["followup-schema-check"].target[0], run.STAGES["followup-schema-apply"].target[0]), ("lifeos.inbox.followup_schema", "lifeos.inbox.followup_apply"))
        codes = json.loads((pathlib.Path(fs.__file__).parent / "codes.json").read_text())
        for code in ("FOLLOWUP_SCHEMA_NOT_READY", "FOLLOWUP_SCHEMA_TABLES_EXIST", "FOLLOWUP_SCHEMA_VERIFY_FAILED"):
            self.assertIn(code, codes)


class Check(unittest.TestCase):
    def test_a_ready_server_reports_true_flags_and_nothing_else(self):
        result = fs.check(Cursor())
        self.assertEqual((result["server"], result["version"], result["row_format"], result["tables_present"], result["ready"]), ("mysql", "8.0", "dynamic", [], True))
        self.assertEqual((result["largest_key_bytes"], result["key_limit_bytes"], result["keys_fit"]), (762, 3072, True))
        self.assertEqual(result["grants"], {"CREATE": True, "SELECT": True, "INSERT": True, "UPDATE": True})
        self.assertNotIn("`x`", repr(result))

    def test_a_compact_server_is_still_ready_because_the_keys_are_small(self):
        result = fs.check(Cursor(row_format="compact"))
        self.assertEqual((result["key_limit_bytes"], result["keys_fit"], result["ready"]), (767, True, True))

    def test_an_unknown_row_format_a_missing_grant_or_an_existing_table_is_visible(self):
        unknown = fs.check(Cursor(row_format="weird"))
        self.assertEqual((unknown["keys_fit"], unknown["ready"]), (None, False))  # cannot tell, so not ready
        no_create = fs.check(Cursor(grants=("GRANT SELECT, INSERT, UPDATE ON `x`.* TO `u`@`%`",)))
        self.assertEqual((no_create["grants"]["CREATE"], no_create["ready"]), (False, False))
        present = fs.check(Cursor(tables=("v7_followup_threads", "v7_other")))
        self.assertEqual(present["tables_present"], ["v7_followup_threads"])  # only this schema's own table names are reported
        self.assertTrue(fs.check(Cursor(grants=("GRANT ALL PRIVILEGES ON `x`.* TO `u`@`%`",)))["ready"])

    def test_a_refused_statement_is_a_none_never_an_error_text(self):
        result = fs.check(Cursor(refuse=("row_format", "GRANTS")))
        self.assertEqual((result["row_format"], result["grants"], result["ready"]), (None, None, False))
        self.assertNotIn("refused", repr(result))

    def test_the_check_only_reads(self):
        cursor = Cursor()
        fs.check(cursor)
        self.assertTrue(all(s.split()[0] in ("SELECT", "SHOW") for s in cursor.sql), cursor.sql)

    def test_the_check_stage_never_writes(self):
        server = Server()
        with mock.patch.object(fs.db, "connect", return_value=stage_cursor(server)):
            result = fs.run(0, True)  # even a live run writes nothing
        self.assertEqual((result["check"]["ready"], result["tables_in_plan"]), (True, 5))
        self.assertTrue(all(s.split()[0] in ("SELECT", "SHOW") for s in server.sql), server.sql)


class Apply(unittest.TestCase):
    def test_apply_creates_the_five_tables_and_reads_them_back(self):
        server = Server()
        result = fa.apply(server)
        self.assertEqual((result["created"], result["verified"], result["rows"]), (sorted(fs.TABLES), True, {t: 0 for t in fs.TABLES}))
        self.assertEqual(len(result["created"]), 5)
        self.assertEqual(result["columns"], fs.column_counts())
        self.assertEqual([s for s in server.sql if s.startswith("CREATE")], fs.plan())  # exactly the planned DDL, nothing else is changed
        self.assertFalse([s for s in server.sql if s.split()[0] in ("DROP", "ALTER", "DELETE", "UPDATE", "INSERT", "TRUNCATE")])

    def test_apply_refuses_a_server_that_is_not_ready(self):
        server = Server(grants=("GRANT SELECT ON `x`.* TO `u`@`%`",))
        with self.assertRaises(fa.SchemaError) as caught:
            fa.apply(server)
        self.assertEqual(str(caught.exception), "FOLLOWUP_SCHEMA_NOT_READY")
        self.assertFalse([s for s in server.sql if s.startswith("CREATE")])

    def test_apply_refuses_when_any_table_exists_and_touches_nothing(self):
        server = Server(tables=("v7_followup_overrides",))
        with self.assertRaises(fa.SchemaError) as caught:
            fa.apply(server)
        self.assertEqual(str(caught.exception), "FOLLOWUP_SCHEMA_TABLES_EXIST")
        self.assertEqual(server.made, ["v7_followup_overrides"])
        self.assertFalse([s for s in server.sql if s.startswith("CREATE")])

    def test_a_failed_read_back_fails_the_stage_with_a_fixed_code(self):
        wrong_columns = dict(fs.column_counts(), v7_followup_threads=15)
        with self.assertRaises(fa.SchemaError) as caught:
            fa.apply(Server(columns=wrong_columns))
        self.assertEqual(str(caught.exception), "FOLLOWUP_SCHEMA_VERIFY_FAILED")
        with self.assertRaises(fa.SchemaError) as caught:
            fa.apply(Server(count=1))
        self.assertEqual(str(caught.exception), "FOLLOWUP_SCHEMA_VERIFY_FAILED")

    def test_the_apply_stage_only_checks_without_the_exact_confirmation_word(self):
        for word in ("", "apply", "APPLY ", "yes"):
            server = Server()
            with mock.patch.object(fa.db, "connect", return_value=stage_cursor(server)), mock.patch.dict("os.environ", {"SCHEMA_CONFIRM": word}):
                result = fa.apply_stage(0, True)
            self.assertEqual((result["dry_run"], result["check"]["ready"]), (True, True), word)
            self.assertFalse([s for s in server.sql if s.startswith("CREATE")], word)
        server = Server()
        with mock.patch.object(fa.db, "connect", return_value=stage_cursor(server)), mock.patch.dict("os.environ", {"SCHEMA_CONFIRM": "APPLY"}):
            applied = fa.apply_stage(0, False)
        self.assertTrue(applied["verified"])
        self.assertEqual([s for s in server.sql if s.startswith("CREATE")], fs.plan())

    def test_every_error_the_module_raises_is_a_fixed_code(self):
        source = pathlib.Path(fa.__file__).read_text()
        raised = {part.split('"')[1] for part in source.split("SchemaError(")[1:] if part.startswith('"')}
        self.assertEqual(raised, {"FOLLOWUP_SCHEMA_NOT_READY", "FOLLOWUP_SCHEMA_TABLES_EXIST", "FOLLOWUP_SCHEMA_VERIFY_FAILED"})


if __name__ == "__main__":
    unittest.main()
