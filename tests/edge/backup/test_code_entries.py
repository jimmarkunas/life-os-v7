"""LIFE-529: the Cloud Fit and resume-builder code folders are in the nightly off-site backup (they run on the Oracle VM only, by design)."""
import tempfile
import unittest
from pathlib import Path

from lifeos.edge.backup import offsite_backup as ob
from tests.edge.backup.test_offsite_backup import local_run

CODE = {p: r for p, r in ob.TREES if p.startswith("code/")}


class Lists(unittest.TestCase):
    def test_both_code_folders_are_listed_and_clear_of_the_deny_pattern(self):
        self.assertEqual(CODE, {"code/lifeos-fit": "/home/ubuntu/.local/lib/lifeos-fit", "code/lifeos-resume": "/home/ubuntu/.local/lib/lifeos-resume"})
        for prefix, root in CODE.items():
            self.assertFalse(ob.denied(prefix, root), prefix)

    def test_the_config_folders_stay_listed(self):
        listed = dict(ob.TREES)
        self.assertEqual(listed["config/lifeos-fit"], "/home/ubuntu/.config/lifeos-fit")
        self.assertEqual(listed["config/lifeos-resume"], "/home/ubuntu/.config/lifeos-resume")


class FitService(unittest.TestCase):
    def test_the_fit_unit_and_deploy_helper_are_listed_optional_and_clear_of_the_deny_pattern(self):
        listed = {name: (source, required) for name, source, required in ob.FILES}
        self.assertEqual(listed["systemd-user/lifeos-fit-gw.service"], ("/home/ubuntu/.config/systemd/user/lifeos-fit-gw.service", False))
        self.assertEqual(listed["sbin/lifeos-fit-deploy"], ("/usr/local/sbin/lifeos-fit-deploy", False))
        for name in ("systemd-user/lifeos-fit-gw.service", "sbin/lifeos-fit-deploy"):
            self.assertFalse(ob.denied(name, listed[name][0]), name)

    def test_the_resume_system_units_are_listed_optional_and_clear_of_the_deny_pattern(self):
        listed = {name: (source, required) for name, source, required in ob.FILES}
        self.assertEqual(listed["systemd/lifeos-resume.socket"], ("/etc/systemd/system/lifeos-resume.socket", False))
        self.assertEqual(listed["systemd/lifeos-resume@.service"], ("/etc/systemd/system/lifeos-resume@.service", False))
        for name in ("systemd/lifeos-resume.socket", "systemd/lifeos-resume@.service"):
            self.assertFalse(ob.denied(name, listed[name][0]), name)

    def test_a_missing_one_is_counted_not_fatal_and_a_present_one_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, present = Path(tmp) / "w", Path(tmp) / "unit.service"
            work.mkdir()
            present.write_text("x")
            files = (("systemd-user/lifeos-fit-gw.service", str(present), False), ("sbin/lifeos-fit-deploy", str(Path(tmp) / "absent"), False))
            entries, extra = ob.collect(str(work), local_run, sqlite=(), files=files, trees=(), secret_trees=(), chown=lambda *a, **k: None, mac_inbox=str(Path(tmp) / "no-inbox"))
            self.assertEqual(sorted(entries), ["systemd-user/lifeos-fit-gw.service"])
            self.assertEqual(extra["missing"], 1)


class Collect(unittest.TestCase):
    def test_code_is_saved_but_rebuildable_folders_and_old_copies_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
            root, work = Path(tmp) / "t", Path(tmp) / "work"
            work.mkdir()
            (root / "node_modules").mkdir(parents=True)
            (root / "node_modules" / "keep.md").write_text("x")
            entries, _ = ob.collect(str(work), local_run, sqlite=(), files=(), trees=(("config/other", str(root)),), secret_trees=(), chown=lambda *a, **k: None)
            self.assertEqual(sorted(entries), ["config/other/node_modules/keep.md"])


if __name__ == "__main__":
    unittest.main()
