"""LIFE-538: the Mac-only Hermes and OpenClaw data is archived, encrypted to the PUBLIC key and pushed to one Oracle inbox."""
import io
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lifeos.edge.backup import mac_push as mp


def make_keys(base):
    """A throwaway keypair: -> (public key file, private homedir). The real private key is never needed or present."""
    home = Path(base) / "priv"
    home.mkdir(mode=0o700)
    subprocess.run(["gpg", "--homedir", str(home), "--batch", "--quiet", "--passphrase", "", "--quick-generate-key", "mac-push-test-key", "default", "default", "never"], check=True, capture_output=True)
    pub = Path(base) / "recipient.asc"
    pub.write_bytes(subprocess.run(["gpg", "--homedir", str(home), "--armor", "--export", "mac-push-test-key"], check=True, capture_output=True).stdout)
    return str(pub), str(home)


def make_home(root):
    home = Path(root) / "home"
    for rel in (".hermes/memories", ".hermes/plugins", ".hermes/cron", ".hermes/hermes-agent", ".hermes/cache", ".openclaw/state", ".openclaw/agents/main/agent", ".openclaw/credentials", ".openclaw/workspace/node_modules", ".config/life-os"):
        (home / rel).mkdir(parents=True)
    for rel in (".hermes/state.db", ".openclaw/state/openclaw.sqlite", ".openclaw/agents/main/agent/openclaw-agent.sqlite"):
        db = sqlite3.connect(home / rel)
        db.execute("create table t(x)")
        db.executemany("insert into t values (?)", [(1,), (2,), (3,)])
        db.commit()
        db.close()
    (home / ".hermes/state.db-wal").write_text("side file")
    (home / ".hermes/config.yaml").write_text("a: 1")
    (home / ".hermes/auth.json").write_text("fake-not-a-secret")
    (home / ".hermes/memories/m.md").write_text("note")
    (home / ".hermes/hermes-agent/big.py").write_text("re-downloadable")
    (home / ".hermes/cache/c.bin").write_text("cache")
    (home / ".hermes/cron/job.json").write_text("{}")
    (home / ".hermes/cron/jobs.lock").write_text("lock")
    (home / ".openclaw/openclaw.json").write_text("{}")
    (home / ".openclaw/credentials/cred.json").write_text("fake-not-a-secret")
    (home / ".openclaw/workspace/w.md").write_text("w")
    (home / ".openclaw/workspace/node_modules/x.js").write_text("x")
    (home / ".config/life-os/newsletter-policy.json").write_text("{}")
    os.symlink(home / ".hermes/config.yaml", home / ".hermes/memories/link.md")
    return home


class Lists(unittest.TestCase):
    def test_every_source_is_under_one_of_the_three_roots_and_names_are_unique(self):
        rels = [r for _, r in mp.SQLITE + mp.FILES + mp.TREES]
        for rel in rels:
            self.assertTrue(rel.startswith((".hermes/", ".openclaw/", ".config/life-os/")), rel)
        names = [n for n, _ in mp.SQLITE + mp.FILES + mp.TREES]
        self.assertEqual(len(names), len(set(names)))

    def test_the_big_redownloadable_folders_are_in_no_list(self):
        for rel in [r for _, r in mp.SQLITE + mp.FILES + mp.TREES]:
            self.assertFalse(set(rel.split("/")) & set(mp.NEVER_FOLDERS), rel)

    def test_the_remote_file_name_pattern(self):
        self.assertTrue(mp.NAME.match("mac-20261109-2350.tar.gz.gpg"))
        for bad in ("mac-1.tar.gz.gpg", "../mac-20261109-2350.tar.gz.gpg", "mac-20261109-2350.tar.gz", "x.part"):
            self.assertFalse(mp.NAME.match(bad), bad)


class Collect(unittest.TestCase):
    def test_databases_files_and_clean_trees_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, work = make_home(tmp), Path(tmp) / "w"
            work.mkdir()
            entries, extra = mp.collect(str(work), str(home))
            names = sorted(entries)
            self.assertIn("hermes/state.db", names)
            self.assertIn("openclaw/credentials/cred.json", names)                           # secret material is collected on purpose (it only ever leaves encrypted)
            self.assertIn("hermes/memories/m.md", names)
            self.assertNotIn("hermes/memories/link.md", names)                               # a symlink is not a regular file
            self.assertFalse([n for n in names if "hermes-agent" in n or "cache" in n or "node_modules" in n or n.endswith(("-wal", ".lock"))])
            self.assertNotEqual(entries["hermes/state.db"], str(home / ".hermes/state.db"))   # a copy, never the live file
            self.assertEqual(sqlite3.connect(entries["hermes/state.db"]).execute("select count(*) from t").fetchone(), (3,))
            self.assertGreater(extra["missing"], 0)                                          # the databases and files this fake home lacks are counted

    def test_nothing_to_save_is_a_fixed_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(mp.PushError) as ctx:
                mp.collect(tmp, tmp)
            self.assertEqual(str(ctx.exception), "mac_backup_nothing_to_save")


class EndToEnd(unittest.TestCase):
    def test_dry_run_encrypts_deletes_the_plain_copy_prints_counts_only_and_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp)
            pub, priv = make_keys(tmp)
            scratch = Path(tempfile.mkdtemp(prefix="mps-", dir="/tmp"))       # short path: gpg's agent socket path has a length limit
            self.addCleanup(lambda: __import__("shutil").rmtree(scratch, ignore_errors=True))
            out = io.StringIO()
            if True:
                kept = {}
                real_encrypt = mp.encrypt

                def spy(plain, dest, work, key_file, run):
                    result = real_encrypt(plain, dest, work, key_file, run)
                    kept["sealed"] = Path(tmp) / "kept.gpg"
                    kept["sealed"].write_bytes(Path(dest).read_bytes())
                    return result
                with mock.patch.object(mp, "encrypt", spy):
                    code = mp.main([], home=str(home), key_file=pub, out=out, tmp_root=str(scratch))
            text = out.getvalue()
            self.assertEqual(code, 0, text)
            self.assertTrue(text.startswith("mac_backup ok dry "), text)
            for word in ("auth", "cred", "token", "fake-not-a-secret", "service-env", ".key", str(home)):
                self.assertNotIn(word, text)                                                 # counts only
            self.assertEqual(list(scratch.iterdir()), [])                                    # plain archive, snapshots and key ring are all gone
            # restore into a scratch folder with the throwaway PRIVATE key, then compare names and open the databases
            plain = Path(tmp) / "restored.tar.gz"
            subprocess.run(["gpg", "--homedir", priv, "--batch", "--quiet", "--output", str(plain), "--decrypt", str(kept["sealed"])], check=True, capture_output=True)
            restore = Path(tmp) / "restore"
            restore.mkdir()
            with tarfile.open(plain) as tar:
                tar.extractall(restore, filter="data")
            self.assertTrue((restore / "hermes/auth.json").is_file())
            self.assertEqual((restore / "hermes/auth.json").read_text(), "fake-not-a-secret")
            for rel in ("hermes/state.db", "openclaw/state/openclaw.sqlite", "openclaw/agents/main/openclaw-agent.sqlite"):
                db = sqlite3.connect(restore / rel)
                self.assertEqual(db.execute("pragma integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("select count(*) from t").fetchone(), (3,))
                db.close()

    def test_a_key_file_that_carries_a_secret_key_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp)
            _, priv = make_keys(tmp)
            both = Path(tmp) / "both.asc"
            both.write_bytes(subprocess.run(["gpg", "--homedir", priv, "--armor", "--export-secret-keys", "mac-push-test-key"], check=True, capture_output=True).stdout)
            out = io.StringIO()
            self.assertEqual(mp.main([], home=str(home), key_file=str(both), out=out), 2)
            self.assertIn("mac_backup_key_has_secret", out.getvalue())

    def test_a_missing_key_is_a_fixed_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            self.assertEqual(mp.main([], home=str(make_home(tmp)), key_file=str(Path(tmp) / "nope.asc"), out=out), 2)
            self.assertIn("mac_backup_key_unreadable", out.getvalue())


class Push(unittest.TestCase):
    def runner(self, existing):
        calls = []

        def run(command, **kwargs):
            batch = kwargs.get("input", "")
            calls.append(batch)
            out = ""
            if "ls -1" in batch:
                out = "".join("in/%s\n" % n for n in sorted(existing))
            return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")
        return run, calls

    def test_it_uploads_under_a_part_name_renames_and_keeps_the_newest_three(self):
        names = ["mac-20261101-0100.tar.gz.gpg", "mac-20261102-0100.tar.gz.gpg", "mac-20261103-0100.tar.gz.gpg", "mac-20261109-0100.tar.gz.gpg"]
        run, calls = self.runner(names)
        result = mp.push("/x/mac-20261109-0100.tar.gz.gpg", run=run)
        self.assertEqual(result, {"remote_kept": 3, "remote_removed": 1})
        self.assertIn("put /x/mac-20261109-0100.tar.gz.gpg mac-20261109-0100.tar.gz.gpg.part", calls[0])
        self.assertIn("rename mac-20261109-0100.tar.gz.gpg.part mac-20261109-0100.tar.gz.gpg", calls[0])
        self.assertIn("rm mac-20261101-0100.tar.gz.gpg", calls[2])
        self.assertEqual(calls[2].count("rm "), 1)

    def test_a_file_that_is_not_listed_after_the_upload_is_an_unverified_push(self):
        run, _ = self.runner(["mac-20261101-0100.tar.gz.gpg"])
        with self.assertRaises(mp.PushError) as ctx:
            mp.push("/x/mac-20261109-0100.tar.gz.gpg", run=run)
        self.assertEqual(str(ctx.exception), "mac_backup_push_unverified")

    def test_a_bad_name_is_refused_before_anything_is_sent(self):
        run, calls = self.runner([])
        with self.assertRaises(mp.PushError):
            mp.push("/x/evil.tar", run=run)
        self.assertEqual(calls, [])

    def test_a_failed_transfer_is_a_fixed_code(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="secret detail that must not surface")
        with self.assertRaises(mp.PushError) as ctx:
            mp.push("/x/mac-20261109-0100.tar.gz.gpg", run=run)
        self.assertEqual(str(ctx.exception), "mac_backup_push_failed")

    def test_the_sftp_command_uses_the_dedicated_key_only(self):
        seen = {}

        def run(command, **kwargs):
            seen["c"] = command
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        mp.sftp("ls\n", run)
        self.assertIn("IdentitiesOnly=yes", seen["c"])
        self.assertIn("BatchMode=yes", seen["c"])
        self.assertIn(mp.SSH_KEY, seen["c"])


if __name__ == "__main__":
    unittest.main()
