"""LIFE-538: the nightly off-site job takes the newest archive the MacBook Pro pushed to the Oracle inbox."""
import os
import tempfile
import unittest
from pathlib import Path

from lifeos.edge.backup import offsite_backup as ob
from tests.edge.backup.test_offsite_backup import local_run


def collect(inbox, work):
    return ob.collect(str(work), local_run, sqlite=(), files=(), trees=(("t", str(work / "tree")),), secret_trees=(), chown=lambda *a, **k: None, mac_inbox=inbox)


class Inbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "w"
        (self.work / "tree").mkdir(parents=True)
        (self.work / "tree" / "keep.md").write_text("x")
        self.inbox = Path(self.tmp.name) / "in"

    def test_only_the_newest_well_named_file_is_taken(self):
        self.inbox.mkdir()
        for name in ("mac-20261101-0100.tar.gz.gpg", "mac-20261109-0100.tar.gz.gpg", "mac-20261109-0100.tar.gz.gpg.part", "other.txt", "mac-1.tar.gz.gpg"):
            (self.inbox / name).write_text("x")
        os.symlink(self.work / "tree" / "keep.md", self.inbox / "mac-20261110-0100.tar.gz.gpg")           # a symlink is never followed
        entries, extra = collect(str(self.inbox), self.work)
        self.assertEqual(sorted(entries), ["mac/mac-20261109-0100.tar.gz.gpg", "t/keep.md"])
        self.assertEqual(extra["missing"], 0)

    def test_an_empty_inbox_is_counted_as_missing(self):
        self.inbox.mkdir()
        entries, extra = collect(str(self.inbox), self.work)
        self.assertEqual(sorted(entries), ["t/keep.md"])
        self.assertEqual(extra["missing"], 1)

    def test_no_inbox_folder_is_not_counted(self):
        entries, extra = collect(str(self.inbox), self.work)
        self.assertEqual(sorted(entries), ["t/keep.md"])
        self.assertEqual(extra["missing"], 0)

    def test_the_inbox_path_and_name_are_clear_of_the_deny_pattern(self):
        self.assertFalse(ob.denied("mac/mac-20261109-0100.tar.gz.gpg", ob.MAC_INBOX + "/mac-20261109-0100.tar.gz.gpg"))
        self.assertEqual(ob.MAC_INBOX, "/srv/mac-inbox/in")


if __name__ == "__main__":
    unittest.main()
