"""LIFE-538: the Mac-only Hermes and OpenClaw data is archived, encrypted to the PUBLIC key and pushed to one Oracle inbox."""
import atexit
import io
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lifeos.edge.backup import mac_push as mp


_KEYS = {}


def _cleanup_keys():
    for root in {v[2] for v in _KEYS.values()}:
        subprocess.run(["gpgconf", "--homedir", root + "/priv", "--kill", "gpg-agent"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


def make_keys(base):
    """A throwaway keypair: -> (public key file, private homedir). The real private key is never needed or present.
    LIFE-527: generated ONCE per test run and shared (key generation was the slowest part of these tests); `base` is accepted for the old call sites and ignored."""
    if "k" not in _KEYS:
        root = tempfile.mkdtemp(prefix="mpk-", dir="/tmp")                    # short path: gpg's agent socket path has a length limit
        home = Path(root) / "priv"
        home.mkdir(mode=0o700)
        subprocess.run(["gpg", "--homedir", str(home), "--batch", "--quiet", "--passphrase", "", "--quick-generate-key", "mac-push-test-key", "default", "default", "never"], check=True, capture_output=True)
        pub = Path(root) / "recipient.asc"
        pub.write_bytes(subprocess.run(["gpg", "--homedir", str(home), "--armor", "--export", "mac-push-test-key"], check=True, capture_output=True).stdout)
        _KEYS["k"] = (str(pub), str(home), root)
        atexit.register(_cleanup_keys)
    return _KEYS["k"][0], _KEYS["k"][1]


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


class Snapshot(unittest.TestCase):
    """LIFE-542: an idle WAL database that mode=ro cannot open is read with immutable=1, and only then."""
    def make(self, tmp, wal=False):
        path = Path(tmp) / "idle.db"
        db = sqlite3.connect(path)
        db.execute("pragma journal_mode=wal")
        db.execute("create table t(x)")
        db.executemany("insert into t values (?)", [(1,), (2,), (3,)])
        db.commit()
        db.close()
        for side in ("-wal", "-shm"):
            if os.path.exists(str(path) + side) and not (wal and side == "-wal"):
                os.remove(str(path) + side)
        return path

    def failing_ro(self, real=sqlite3.connect):
        seen = []

        def connect(target, *args, **kwargs):
            seen.append(target)
            if "mode=ro" in str(target):
                raise sqlite3.OperationalError("unable to open database file")        # what an idle WAL database does to a read-only open
            return real(target, *args, **kwargs)
        return connect, seen

    def test_an_idle_wal_database_is_copied_through_the_immutable_retry_and_passes_the_integrity_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, dest = self.make(tmp), Path(tmp) / "copy.sqlite"
            connect, seen = self.failing_ro()
            with mock.patch.object(sqlite3, "connect", connect):
                mp.snapshot(str(source), str(dest))
            self.assertTrue(any("mode=ro" in s for s in seen) and any("immutable=1" in s for s in seen))
            copy = sqlite3.connect(dest)
            self.assertEqual(copy.execute("select count(*) from t").fetchone(), (3,))
            self.assertEqual(copy.execute("pragma integrity_check").fetchone()[0], "ok")
            copy.close()

    def test_no_retry_when_a_wal_file_exists_the_database_is_not_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.make(tmp)
            Path(str(source) + "-wal").write_bytes(b"")
            connect, seen = self.failing_ro()
            with mock.patch.object(sqlite3, "connect", connect), self.assertRaises(mp.PushError) as ctx:
                mp.snapshot(str(source), str(Path(tmp) / "copy.sqlite"))
            self.assertEqual(str(ctx.exception), "mac_backup_db_unreadable")
            self.assertFalse(any("immutable" in s for s in seen))

    def test_a_normal_database_never_uses_the_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.make(tmp)
            seen = []
            real = sqlite3.connect

            def connect(target, *args, **kwargs):
                seen.append(str(target))
                return real(target, *args, **kwargs)
            with mock.patch.object(sqlite3, "connect", connect):
                mp.snapshot(str(source), str(Path(tmp) / "copy.sqlite"))
            self.assertFalse(any("immutable" in s for s in seen))

    def test_a_missing_or_corrupt_file_is_still_a_fixed_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.db"
            bad.write_bytes(b"this is not a database" * 50)
            for source in (bad, Path(tmp) / "absent.db"):
                with self.assertRaises(mp.PushError) as ctx:
                    mp.snapshot(str(source), str(Path(tmp) / "copy.sqlite"))
                self.assertEqual(str(ctx.exception), "mac_backup_db_unreadable")


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
