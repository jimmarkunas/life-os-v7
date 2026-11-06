"""LIFE-331: the OpenClaw avatar install. A fake `openclaw` stands in for the real program and logs what it was asked to do."""
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCRIPT = os.path.join(ROOT, "lifeos", "edge", "openclaw", "deploy_avatar.sh")
REAL_MANIFEST = os.path.join(ROOT, "lifeos", "edge", "openclaw", "avatar.manifest")
PNG = b"\x89PNG\r\n\x1a\n" + b"fake animated png body " * 20
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
open(os.environ["FAKE_LOG"], "a").write(" ".join(sys.argv[1:]) + "\n")
a = sys.argv[1:]
if a[:2] == ["agents", "set-identity"]:
    conf = os.environ["FAKE_CONF"]
    cfg = json.load(open(conf))
    cfg["agents"]["entries"][a[a.index("--agent") + 1]]["identity"]["avatar"] = a[a.index("--avatar") + 1]
    json.dump(cfg, open(conf, "w"), indent=2)
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Deploy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        self.state = os.path.join(self.home, ".openclaw")
        self.ws = os.path.join(self.state, "workspace")
        os.makedirs(os.path.join(self.ws, "avatars"))
        self.conf = os.path.join(self.state, "openclaw.json")
        self.old_conf = json.dumps({"agents": {"entries": {"main": {"identity": {"avatar": "data:image/png;base64," + "A" * 500}}, "fit": {"identity": {"name": "Fit"}}}},
                                    "gateway": {"auth": {"token": "SECRETVALUE"}}}, indent=2)
        with open(self.conf, "w") as handle:
            handle.write(self.old_conf)
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        self.image = os.path.join(self.src, "openclaw-256.apng")
        self.write(self.image, PNG)
        self.manifest = os.path.join(self.tmp, "avatar.manifest")
        self.set_manifest(PNG)
        self.log = os.path.join(self.tmp, "calls.log")
        self.fake = os.path.join(self.tmp, "openclaw")
        self.write(self.fake, FAKE.encode())
        os.chmod(self.fake, 0o755)
        self.backup = os.path.join(self.tmp, "backup")

    def write(self, path, data):
        with open(path, "wb") as handle:
            handle.write(data)

    def set_manifest(self, data, size=None):
        self.write(self.manifest, ("# test\nmain openclaw-256.apng %s %d\n" % (sha(data), len(data) if size is None else size)).encode())

    def run_script(self, *args):
        env = dict(os.environ, FAKE_LOG=self.log, FAKE_CONF=self.conf)
        done = subprocess.run(["bash", SCRIPT, "--src", self.src, "--home", self.home, "--manifest", self.manifest, "--backup", self.backup,
                               "--user", getpass.getuser(), "--bin", self.fake, *args], capture_output=True, text=True, env=env)
        return done.returncode, done.stdout + done.stderr

    def calls(self):
        return open(self.log).read() if os.path.exists(self.log) else ""

    def avatar(self):
        return json.load(open(self.conf))["agents"]["entries"]["main"]["identity"]["avatar"]

    def test_the_real_manifest_names_the_avatar_with_a_hash_and_a_size_under_the_limit(self):
        lines = [l.split() for l in open(REAL_MANIFEST) if l.strip() and not l.startswith("#")]
        self.assertEqual(len(lines), 1)
        agent, name, digest, size = lines[0]
        self.assertEqual((agent, name), ("main", "openclaw-256.apng"))
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertLessEqual(int(size), 2_000_000)
        self.assertEqual(subprocess.run(["bash", "-n", SCRIPT]).returncode, 0)

    def test_dry_run_checks_the_image_names_what_would_change_and_writes_nothing(self):
        status, out = self.run_script()
        self.assertEqual(status, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn("matches the manifest", out)
        self.assertIn("inline picture (data URI, 522 characters)", out)
        self.assertIn("Dry run only", out)
        self.assertNotIn("SECRETVALUE", out)
        self.assertEqual(open(self.conf).read(), self.old_conf)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "avatars", "openclaw.png")))
        self.assertFalse(os.path.exists(self.backup))
        self.assertEqual(self.calls(), "")

    def test_apply_backs_up_installs_sets_the_path_with_openclaws_own_command_and_reads_back(self):
        status, out = self.run_script("--apply")
        self.assertEqual(status, 0, out)
        dest = os.path.join(self.ws, "avatars", "openclaw.png")
        self.assertEqual(open(dest, "rb").read(), PNG)
        self.assertEqual(os.stat(dest).st_mode & 0o777, 0o644)
        self.assertEqual(self.avatar(), "avatars/openclaw.png")
        self.assertEqual(self.calls().strip(), "agents set-identity --agent main --avatar avatars/openclaw.png")
        self.assertEqual(open(os.path.join(self.backup, "openclaw.json")).read(), self.old_conf)
        self.assertIn("read-back OK", out)
        self.assertIn("ROLLBACK config: cp -p %s" % os.path.join(self.backup, "openclaw.json"), out)
        self.assertIn("ROLLBACK image:  rm -f " + dest, out)
        self.assertIn("Nothing was restarted", out)

    def test_a_second_apply_keeps_the_existing_image_in_the_backup(self):
        dest = os.path.join(self.ws, "avatars", "openclaw.png")
        self.write(dest, b"older image")
        status, out = self.run_script("--apply")
        self.assertEqual(status, 0, out)
        self.assertEqual(open(os.path.join(self.backup, "openclaw.png"), "rb").read(), b"older image")
        self.assertIn("ROLLBACK image:  cp -p %s %s" % (os.path.join(self.backup, "openclaw.png"), dest), out)

    def test_bad_inputs_are_refused_before_anything_is_written(self):
        def refuse(needle, mutate):
            shutil.rmtree(self.backup, ignore_errors=True)
            mutate()
            status, out = self.run_script("--apply")
            self.assertEqual(status, 1, (needle, out))
            self.assertIn(needle, out)
            self.assertEqual(self.calls(), "", needle)
            if os.path.exists(self.conf):
                self.assertEqual(open(self.conf).read(), self.old_conf, needle)
            self.assertFalse(os.path.exists(self.backup), needle)
        refuse("hashes to", lambda: self.write(self.image, PNG + b"x") or self.set_manifest(PNG, len(PNG) + 1))
        self.write(self.image, PNG)
        refuse("not a PNG" if False else "does not start like a PNG", lambda: (self.write(self.image, b"GIF89a" + b"0" * 50), self.set_manifest(b"GIF89a" + b"0" * 50)))
        self.write(self.image, PNG)
        self.set_manifest(PNG)
        refuse("at most 2000000", lambda: (self.write(self.manifest, ("main openclaw-256.apng %s 3000000\n" % sha(PNG)).encode()), self.write(self.image, PNG + b"0" * 2_999_000)))
        self.write(self.image, PNG)
        self.set_manifest(PNG)
        refuse("no line for agent", lambda: self.write(self.manifest, b"# nothing\n"))
        self.set_manifest(PNG)
        refuse("is not there", lambda: os.remove(self.image))
        self.write(self.image, PNG)
        refuse("symbolic link", lambda: (os.remove(self.image), os.symlink("/etc/hostname", self.image)))
        os.remove(self.image)
        self.write(self.image, PNG)
        refuse("no config at", lambda: os.rename(self.conf, self.conf + ".gone"))
        os.rename(self.conf + ".gone", self.conf)
        status, out = subprocess.run(["bash", SCRIPT], capture_output=True, text=True).returncode, ""
        self.assertEqual(status, 1)

    def test_a_failed_read_back_says_so_and_prints_the_rollback(self):
        self.write(self.fake, b"#!/bin/bash\necho ran >> \"$FAKE_LOG\"\n")           # a command that changes nothing
        status, out = self.run_script("--apply")
        self.assertEqual(status, 1, out)
        self.assertIn("READ-BACK FAILED", out)
        self.assertIn("ROLLBACK: cp -p", out)

    def test_the_script_restarts_nothing_and_touches_no_other_setting(self):
        code = "\n".join(l for l in open(SCRIPT).read().splitlines() if not l.lstrip().startswith("#"))
        for pattern in (r"systemctl", r"\bkill\b", r"\breboot\b", r"gateway\s+(restart|stop)", r"agents\s+(add|delete|remove)", r"config\s+set"):
            self.assertIsNone(re.search(pattern, code), pattern)
        self.assertEqual(len(re.findall(r"set-identity", code)), 3)                # dry-run text, direct call, sudo call


if __name__ == "__main__":
    unittest.main()
