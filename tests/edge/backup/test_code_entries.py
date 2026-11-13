"""LIFE-529: the Cloud Fit and resume-builder code folders are in the nightly off-site backup (they run on the Oracle VM only, by design)."""
import unittest
from pathlib import Path

from lifeos.edge.backup import offsite_backup as ob
from tests.edge.backup import safe_tmp
from tests.edge.backup.test_offsite_backup import local_run

class FitService(unittest.TestCase):
    def test_a_missing_one_is_counted_not_fatal_and_a_present_one_is_saved(self):
        with safe_tmp.TemporaryDirectory() as tmp:
            work, present = Path(tmp) / "w", Path(tmp) / "unit.service"
            work.mkdir()
            present.write_text("x")
            files = (("systemd-user/lifeos-fit-gw.service", str(present), False), ("sbin/lifeos-fit-deploy", str(Path(tmp) / "absent"), False))
            entries, extra = ob.collect(str(work), local_run, sqlite=(), files=files, trees=(), secret_trees=(), chown=lambda *a, **k: None, mac_inbox=str(Path(tmp) / "no-inbox"))
            self.assertEqual(sorted(entries), ["systemd-user/lifeos-fit-gw.service"])
            self.assertEqual(extra["missing"], 1)


class Collect(unittest.TestCase):
    def test_code_is_saved_but_rebuildable_folders_and_old_copies_are_not(self):
        with safe_tmp.TemporaryDirectory() as tmp:
            root, work = Path(tmp) / "code", Path(tmp) / "work"
            work.mkdir()
            (root / "node_modules" / "docx").mkdir(parents=True)
            (root / "__pycache__").mkdir()
            (root / "sub").mkdir()
            for name in ("front.py", "gw.mjs", "package-lock.json", "sub/inner.py", "front.py.bak-before-x", "node_modules/docx/index.js", "__pycache__/front.pyc"):
                (root / name).write_text("x")
            entries, extra = ob.collect(str(work), local_run, sqlite=(), files=(), trees=(("code/x", str(root)),), secret_trees=(), chown=lambda *a, **k: None)
            self.assertEqual(sorted(entries), ["code/x/front.py", "code/x/gw.mjs", "code/x/package-lock.json", "code/x/sub/inner.py"])
            self.assertEqual(extra["skipped"], 1)                                             # the old .bak copy

    def test_other_trees_are_still_walked_in_full(self):
        with safe_tmp.TemporaryDirectory() as tmp:
            root, work = Path(tmp) / "t", Path(tmp) / "work"
            work.mkdir()
            (root / "node_modules").mkdir(parents=True)
            (root / "node_modules" / "keep.md").write_text("x")
            entries, _ = ob.collect(str(work), local_run, sqlite=(), files=(), trees=(("config/other", str(root)),), secret_trees=(), chown=lambda *a, **k: None)
            self.assertEqual(sorted(entries), ["config/other/node_modules/keep.md"])


if __name__ == "__main__":
    unittest.main()
