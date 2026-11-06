import io
import os
import shutil
import sqlite3
import tarfile
import tempfile
import unittest

from lifeos.edge.backup import verify_restore as vr


def make_db(path, rows):
    db = sqlite3.connect(path)
    db.execute("create table sessions (id integer)")
    db.executemany("insert into sessions values (?)", [(i,) for i in range(rows)])
    db.commit()
    db.close()


class Verify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def archive(self, extra=None, corrupt=False):
        src = os.path.join(self.tmp, "src")
        os.makedirs(os.path.join(src, "hermes", "profiles"), exist_ok=True)
        make_db(os.path.join(src, "hermes", "state.db"), 5)
        make_db(os.path.join(src, "hermes", "profiles", "role.db"), 2)
        with open(os.path.join(src, "hermes", "config.yaml"), "w") as handle:
            handle.write("a: 1\n")
        make_db(os.path.join(src, "other.sqlite"), 9)
        if corrupt:
            with open(os.path.join(src, "hermes", "state.db"), "r+b") as handle:
                handle.seek(0)
                handle.write(b"this is not a database at all, just text")
        path = os.path.join(self.tmp, "a.tar.gz")
        with tarfile.open(path, "w:gz") as tar:
            for folder, _dirs, names in os.walk(src):
                for name in names:
                    full = os.path.join(folder, name)
                    tar.add(full, arcname=os.path.relpath(full, src))
            if extra:
                extra(tar)
        return path

    def test_a_good_archive_reports_files_integrity_and_row_counts(self):
        report = vr.verify(self.archive())
        self.assertEqual(report["files"], 3)
        self.assertEqual(report["databases"]["hermes/state.db"], {"sessions": 5})
        self.assertEqual(report["databases"]["hermes/profiles/role.db"], {"sessions": 2})
        self.assertNotIn("other.sqlite", " ".join(report["databases"]))                 # outside the prefix: not examined

    def test_the_check_folder_is_removed_and_main_prints_counts_and_no_content(self):
        out = io.StringIO()
        before = set(os.listdir(tempfile.gettempdir()))
        self.assertEqual(vr.main([self.archive()], out), 0)
        self.assertIn("databases: 2", out.getvalue())
        self.assertIn("sessions=5", out.getvalue())
        self.assertNotIn("a: 1", out.getvalue())
        self.assertFalse([n for n in set(os.listdir(tempfile.gettempdir())) - before if n.startswith("restore-check-")])

    def test_a_corrupt_database_is_rejected(self):
        path = self.archive(corrupt=True)
        with self.assertRaises(vr.RestoreError) as raised:
            vr.verify(path)
        self.assertEqual(str(raised.exception), "RESTORE_DB_CORRUPT")
        self.assertEqual(vr.main([path], io.StringIO()), 1)

    def test_a_failure_names_the_database_and_every_bad_database_is_reported_in_one_pass(self):
        path = self.archive(corrupt=True)
        with self.assertRaises(vr.RestoreError) as raised:
            vr.verify(path)
        self.assertIn("hermes/state.db: RESTORE_DB_CORRUPT", raised.exception.detail)
        self.assertNotIn("role.db", raised.exception.detail)                          # the good one is not blamed
        out = io.StringIO()
        self.assertEqual(vr.main([path], out), 1)
        self.assertIn("RESTORE FAILED: RESTORE_DB_CORRUPT", out.getvalue())
        self.assertIn("  hermes/state.db: RESTORE_DB_CORRUPT", out.getvalue())

    def test_two_bad_databases_are_both_named_and_a_missing_module_is_not_called_corruption(self):
        real = vr.tables

        def fake(path):
            if path.endswith("state.db"):
                raise vr.RestoreError("RESTORE_DB_UNCHECKED", "no such module: vec0")
            if path.endswith("role.db"):
                raise vr.RestoreError("RESTORE_DB_CORRUPT", "database disk image is malformed")
            return real(path)
        vr.tables = fake
        self.addCleanup(setattr, vr, "tables", real)
        archive = self.archive()
        with self.assertRaises(vr.RestoreError) as raised:
            vr.verify(archive)
        self.assertIn("hermes/state.db: RESTORE_DB_UNCHECKED (no such module: vec0)", raised.exception.detail)
        self.assertIn("hermes/profiles/role.db: RESTORE_DB_CORRUPT (database disk image is malformed)", raised.exception.detail)
        self.assertEqual(str(raised.exception), "RESTORE_DB_CORRUPT")                  # mixed causes: the worse code leads
        vr.tables = lambda path: (_ for _ in ()).throw(vr.RestoreError("RESTORE_DB_UNCHECKED", "no such module: vec0"))
        with self.assertRaises(vr.RestoreError) as only:
            vr.verify(archive)
        self.assertEqual(str(only.exception), "RESTORE_DB_UNCHECKED")

    def test_sqlite_error_text_is_classified(self):
        db = os.path.join(self.tmp, "x.db")
        with open(db, "wb") as handle:
            handle.write(b"not a database " * 50)
        with self.assertRaises(vr.RestoreError) as raised:
            vr.tables(db)
        self.assertEqual(str(raised.exception), "RESTORE_DB_CORRUPT")
        self.assertTrue(raised.exception.detail)

    def test_an_unsafe_member_is_rejected_before_anything_is_written(self):
        def evil(tar):
            data = b"x"
            info = tarfile.TarInfo("hermes/../../evil.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with self.assertRaises(vr.RestoreError) as raised:
            vr.verify(self.archive(extra=evil))
        self.assertEqual(str(raised.exception), "RESTORE_UNSAFE_PATH")

    def test_a_link_member_is_rejected(self):
        def link(tar):
            info = tarfile.TarInfo("hermes/link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        with self.assertRaises(vr.RestoreError) as raised:
            vr.verify(self.archive(extra=link))
        self.assertEqual(str(raised.exception), "RESTORE_UNSAFE_PATH")

    def test_nothing_under_the_prefix_and_an_unreadable_file_are_fixed_codes(self):
        with self.assertRaises(vr.RestoreError) as raised:
            vr.verify(self.archive(), prefix="nothing/")
        self.assertEqual(str(raised.exception), "RESTORE_NOTHING_UNDER_PREFIX")
        bad = os.path.join(self.tmp, "bad.tar.gz")
        with open(bad, "wb") as handle:
            handle.write(b"not a tar")
        with self.assertRaises(vr.RestoreError) as raised:
            vr.verify(bad)
        self.assertEqual(str(raised.exception), "RESTORE_UNREADABLE")

    def test_usage_errors_return_2(self):
        self.assertEqual(vr.main([], io.StringIO()), 2)


if __name__ == "__main__":
    unittest.main()
