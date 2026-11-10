"""LIFE-538 / LIFE-529: the one-command real-key restore check (restore_check.py). Throwaway keys only; the real key and archive are never used here."""
import contextlib
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
from lifeos.edge.backup import restore_check as rc
from tests.edge.backup.test_mac_push import make_home

UID = "restore-check-test-key"


def make_pair(base, passphrase=""):
    home = Path(base) / ("priv-" + ("pp" if passphrase else "np"))
    home.mkdir(mode=0o700)
    subprocess.run(["gpg", "--homedir", str(home), "--batch", "--quiet", "--pinentry-mode", "loopback", "--passphrase", passphrase, "--quick-generate-key", UID, "default", "default", "never"], check=True, capture_output=True)
    pub = Path(base) / ("pub-%s.asc" % ("pp" if passphrase else "np"))
    pub.write_bytes(subprocess.run(["gpg", "--homedir", str(home), "--armor", "--export", UID], check=True, capture_output=True).stdout)
    secret = subprocess.run(["gpg", "--homedir", str(home), "--batch", "--pinentry-mode", "loopback", "--passphrase", passphrase, "--armor", "--export-secret-keys", UID], check=True, capture_output=True).stdout
    return str(pub), secret


def seal(pub, plain, dest):
    home = tempfile.mkdtemp(prefix="sealk-", dir="/tmp")
    try:
        subprocess.run(["gpg", "--homedir", home, "--batch", "--quiet", "--import", pub], check=True, capture_output=True)
        subprocess.run(["gpg", "--homedir", home, "--batch", "--quiet", "--yes", "--trust-model", "always", "--output", dest, "--encrypt", "--recipient", UID, plain], check=True, capture_output=True)
    finally:
        shutil.rmtree(home, ignore_errors=True)


@contextlib.contextmanager
def fake_ram(*a, **k):
    path = tempfile.mkdtemp(prefix="rcram-", dir="/tmp")
    os.chmod(path, 0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def build(base, pub):
    """-> (nightly archive path, code dir). A nightly tar.gz with a Mac archive inside, then both sealed."""
    base = Path(base)
    home = make_home(base)
    work = base / "w"
    work.mkdir()
    entries, _ = mp.collect(str(work), str(home))
    inner_plain = base / "inner.tar.gz"
    mp.build_archive(entries, str(inner_plain))
    inner_sealed = base / "mac-20261109-2308.tar.gz.gpg"
    seal(pub, str(inner_plain), str(inner_sealed))
    code = base / "code"
    code.mkdir()
    (code / "front.py").write_text("print('x')")
    (code / "sub").mkdir()
    (code / "sub" / "gw.mjs").write_text("export {}")
    outer = base / "nightly.tar.gz"
    db = base / "plain.sqlite"
    sql = sqlite3.connect(db)
    sql.execute("create table t(x)")
    sql.execute("insert into t values (1)")
    sql.commit()
    sql.close()
    with tarfile.open(outer, "w:gz") as tar:
        tar.add(inner_sealed, arcname="mac/mac-20261109-2308.tar.gz.gpg")
        tar.add(code / "front.py", arcname="code/lifeos-fit/front.py")
        tar.add(code / "sub" / "gw.mjs", arcname="code/lifeos-fit/sub/gw.mjs")
        tar.add(db, arcname="grans/grans.db")
    sealed = base / "lifeos-edge-test.tar.gz.gpg"
    seal(pub, str(outer), str(sealed))
    return str(sealed), code


def key_source(secret):
    return mock.patch.object(rc, "fetch_key", lambda source, run=subprocess.run: secret)


class Run(unittest.TestCase):
    def test_pass_with_counts_only_for_the_nightly_and_the_mac_archive_inside_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            pub, secret = make_pair(tmp)
            archive, code = build(tmp, pub)
            out = io.StringIO()
            with key_source(secret):
                self.assertEqual(rc.run_check(archive, ("bws", "x"), ram=fake_ram, out=out, manifests=("code/lifeos-fit/",)), 0)
            text = out.getvalue()
            self.assertTrue(text.startswith("PASS "), text)
            self.assertIn("archives=2", text)
            self.assertIn("mac_databases=3", text)
            self.assertIn("nightly_databases=1", text)
            for word in ("front.py", "gw.mjs", "auth", "cred", "token", "fake-not-a-secret", UID, "state.db", "BEGIN PGP"):
                self.assertNotIn(word, text)                                                 # counts only
            self.assertIn("nightly_manifest_code_lifeos-fit=2 files", text)
            n, h = rc.manifest_hash(str(Path(tmp) / "nightly.tar.gz"), "code/lifeos-fit/")
            self.assertIn(h[:16], text)

    def test_a_passphrase_protected_key_works_with_its_passphrase_from_bitwarden(self):
        with tempfile.TemporaryDirectory() as tmp:
            pub, secret = make_pair(tmp, "correct horse")
            archive, _ = build(tmp, pub)

            def fetch(source, run=subprocess.run):
                return secret if source[1] == "key" else b"correct horse"
            out = io.StringIO()
            with mock.patch.object(rc, "fetch_key", fetch):
                self.assertEqual(rc.run_check(archive, ("bws", "key"), ram=fake_ram, out=out, passphrase=("bws", "pp")), 0, out.getvalue())
            wrong = io.StringIO()
            with mock.patch.object(rc, "fetch_key", lambda s, run=subprocess.run: secret if s[1] == "key" else b"wrong"):
                self.assertEqual(rc.run_check(archive, ("bws", "key"), ram=fake_ram, out=wrong, passphrase=("bws", "pp")), 1)
            self.assertEqual(wrong.getvalue(), "FAIL restore_decrypt\n")

    def test_a_key_that_does_not_open_the_archive_is_a_fixed_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            pub, _ = make_pair(tmp)
            archive, _ = build(tmp, pub)
            other = Path(tmp) / "other"
            other.mkdir()
            _, other_secret = make_pair(other)
            out = io.StringIO()
            with key_source(other_secret):
                self.assertEqual(rc.run_check(archive, ("bws", "x"), ram=fake_ram, out=out), 1)
            self.assertEqual(out.getvalue(), "FAIL restore_decrypt\n")

    def test_the_folder_is_gone_after_pass_and_after_fail(self):
        made = []

        @contextlib.contextmanager
        def tracking(*a, **k):
            with fake_ram() as path:
                made.append(path)
                yield path
        with tempfile.TemporaryDirectory() as tmp:
            pub, secret = make_pair(tmp)
            archive, _ = build(tmp, pub)
            with key_source(secret):
                rc.run_check(archive, ("bws", "x"), ram=tracking, out=io.StringIO())
            with key_source(b"not a key"):
                self.assertEqual(rc.run_check(archive, ("bws", "x"), ram=tracking, out=io.StringIO()), 1)
        self.assertEqual(len(made), 2)
        self.assertFalse([p for p in made if os.path.exists(p)])

    def test_a_plain_file_name_is_refused_and_a_missing_source_is_usage(self):
        out = io.StringIO()
        self.assertEqual(rc.run_check("backup.tar.gz", ("bws", "x"), ram=fake_ram, out=out), 1)
        with mock.patch.object(rc, "ID_FILE", "/nonexistent/id"):
            self.assertEqual(rc.main(["backup.tar.gz.gpg"], out=io.StringIO()), 2)


class DefaultId(unittest.TestCase):
    def test_without_an_id_on_the_command_line_the_local_file_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            idfile = Path(tmp) / "id"
            idfile.write_text("the-id\n")
            seen = {}

            def fake_run_check(archive, source, **kwargs):
                seen["source"] = source
                return 0
            with mock.patch.object(rc, "ID_FILE", str(idfile)), mock.patch.object(rc, "run_check", fake_run_check):
                self.assertEqual(rc.main(["backup.tar.gz.gpg"], out=io.StringIO()), 0)
            self.assertEqual(seen["source"], ("bws", "the-id"))

    def test_without_the_file_or_an_id_it_is_usage(self):
        with mock.patch.object(rc, "ID_FILE", "/nonexistent/id"):
            self.assertEqual(rc.main(["backup.tar.gz.gpg"], out=io.StringIO()), 2)

    def test_an_id_on_the_command_line_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            idfile = Path(tmp) / "id"
            idfile.write_text("from-file")
            seen = {}
            with mock.patch.object(rc, "ID_FILE", str(idfile)), mock.patch.object(rc, "run_check", lambda a, s, **k: seen.setdefault("s", s) and 0):
                rc.main(["backup.tar.gz.gpg", "--bws-secret", "from-cli"], out=io.StringIO())
            self.assertEqual(seen["s"], ("bws", "from-cli"))


class Fetch(unittest.TestCase):
    def runner(self, code, stdout):
        seen = {}

        def run(command, **kwargs):
            seen["c"], seen["k"] = command, kwargs
            return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=b"detail that must never surface")
        return run, seen

    def test_bws_reads_the_value_field_and_asks_for_json_only(self):
        run, seen = self.runner(0, b'{"id":"1","value":"KEYTEXT"}')
        self.assertEqual(rc.fetch_key(("bws", "SECRET-ID"), run), b"KEYTEXT")
        self.assertEqual(seen["c"][:3], ["bws", "secret", "get"])

    def test_bw_reads_the_item_notes(self):
        run, seen = self.runner(0, b"KEYTEXT\n")
        self.assertEqual(rc.fetch_key(("bw", "ITEM"), run), b"KEYTEXT\n")
        self.assertEqual(seen["c"], ["bw", "get", "notes", "ITEM"])

    def test_failures_are_fixed_codes(self):
        for source, run in ((("bws", "x"), self.runner(1, b"")[0]), (("bws", "x"), self.runner(0, b"not json")[0]), (("bw", "x"), self.runner(0, b"")[0])):
            with self.assertRaises(rc.CheckError) as ctx:
                rc.fetch_key(source, run)
            self.assertEqual(str(ctx.exception), "restore_key_fetch")
        with self.assertRaises(rc.CheckError) as ctx:
            rc.fetch_key(("other", "x"))
        self.assertEqual(str(ctx.exception), "restore_key_source")


class Ram(unittest.TestCase):
    def test_an_unknown_platform_never_falls_back_to_disk(self):
        with self.assertRaises(rc.CheckError) as ctx:
            with rc.ram_folder(platform="win32"):
                pass
        self.assertEqual(str(ctx.exception), "restore_no_ram")

    def test_mac_ram_disk_failure_is_a_fixed_code_and_nothing_is_attached(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command[0])
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        with self.assertRaises(rc.CheckError):
            with rc.ram_folder(run=run, platform="darwin"):
                pass
        self.assertEqual(calls, ["hdiutil"])

    def test_mac_ram_disk_is_detached_even_when_the_body_fails(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command[0])
            if command[0] == "hdiutil" and command[1] == "attach":
                return subprocess.CompletedProcess(command, 0, stdout="/dev/disk9   \n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        with mock.patch.object(rc.os, "chmod"), mock.patch.object(rc.shutil, "rmtree"):
            with self.assertRaises(ValueError):
                with rc.ram_folder(run=run, platform="darwin"):
                    raise ValueError("boom")
        self.assertEqual(calls, ["hdiutil", "diskutil", "hdiutil"])


if __name__ == "__main__":
    unittest.main()
