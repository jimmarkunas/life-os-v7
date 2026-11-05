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
