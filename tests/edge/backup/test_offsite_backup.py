import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

from lifeos.edge.backup import consent
from lifeos.edge.backup import offsite_backup as ob

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / "lifeos" / "edge" / "backup"
CRED = json.dumps({"client_id": "id.example", "client_secret": "not-a-real-secret", "refresh_token": "not-a-real-refresh"})
HAVE_GPG = shutil.which("gpg") is not None


def local_run(command, **kwargs):
    """Stand-in for `runuser -u OWNER -- python3 ...`: the same snapshot code, run as the test's own user."""
    if command[0] == "runuser":
        command = [sys.executable] + command[command.index("python3") + 1:]
    return subprocess.run(command, **kwargs)


def make_db(path, rows=3):
    db = sqlite3.connect(path)
    db.execute("pragma journal_mode=wal")
    db.execute("create table t (v text)")
    db.executemany("insert into t values (?)", [(f"row{i}",) for i in range(rows)])
    db.commit()
    return db


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = Path(self.tmp) / "src"
        self.src.mkdir()
        self.work = Path(self.tmp) / "work"
        self.work.mkdir()

    def lists(self, required=True):
        make_db(self.src / "a.sqlite").close()
        (self.src / "conf.json").write_text("{}")
        tree = self.src / "tree"
        (tree / "sub").mkdir(parents=True)
        (tree / "keep.md").write_text("keep")
        (tree / "sub" / "also.txt").write_text("also")
        (tree / "old.json.bak-1").write_text("old")           # a leftover copy: not collected
        (tree / "my-token.txt").write_text("secret")           # secret-shaped name: not collected
        os.symlink(self.src / "conf.json", tree / "link.json")
        vault = self.src / "vault"
        vault.mkdir()
        (vault / "token-a.json").write_text("v1")             # credential material from the one chosen folder: collected
        (vault / "auth-b.json").write_text("v2")
        os.symlink(self.src / "conf.json", vault / "link.json")
        return dict(secret_trees=(("v", str(vault)),), sqlite=(("db/a.sqlite", str(self.src / "a.sqlite"), "me", required),),
                    files=(("conf/conf.json", str(self.src / "conf.json"), required),),
                    trees=(("t", str(tree)),), chown=lambda *a, **k: None)


class Manifest(unittest.TestCase):
    def test_no_listed_source_or_name_is_secret_shaped(self):
        for name, source, *_ in ob.SQLITE + ob.FILES:
            self.assertFalse(ob.denied(name, source), name)
        for prefix, root in ob.TREES:
            self.assertFalse(ob.denied(prefix, root), prefix)

    def test_credential_material_comes_only_from_the_one_chosen_folder(self):
        listed = " ".join(s for _, s, *_ in ob.SQLITE + ob.FILES) + " " + " ".join(r for _, r in ob.TREES)
        for word in ("credentials", "/.config/bws", "/.config/gh", "/.config/notion", "auth.toml", "db-key", "hash-key", "event-key", "credstore"):
            self.assertNotIn(word, listed)
        self.assertEqual([root for _, root in ob.SECRET_TREES], ["/home/ubuntu/.openclaw/credentials"])

    def test_the_deny_pattern_catches_what_it_is_for(self):
        for name in ("a/credentials/x", "x/my-token.txt", "auth.toml", "db-key", ".env", "x/secret.json", "bws/openclaw.env", "f.bak-1", "p.pem", "/home/u/.config/gh/hosts.yml"):
            self.assertTrue(ob.denied(name), name)
        for name in ("openclaw/openclaw.json", "config/lifeos-resume/rules.md", "grans/grans.db"):
            self.assertFalse(ob.denied(name), name)

    def test_the_file_name_is_central_twelve_hour(self):
        self.assertEqual(ob.now_central(lambda: 1793145932), "2026-10-27_0705PM_CT")      # daylight time
        self.assertEqual(ob.now_central(lambda: 1797325800), "2026-12-15_0310AM_CT")      # standard time

    def test_the_two_scope_constants_agree_and_are_narrow(self):
        self.assertEqual(ob.SCOPE, consent.SCOPE)
        self.assertTrue(ob.SCOPE.endswith("/drive.file"))


class Collect(Tmp):
    def test_collects_snapshots_files_and_clean_trees_only(self):
        entries, extra = ob.collect(str(self.work), local_run, **self.lists())
        self.assertEqual(sorted(entries), ["conf/conf.json", "db/a.sqlite", "t/keep.md", "t/sub/also.txt", "v/auth-b.json", "v/token-a.json"])
        self.assertEqual(extra, {"missing": 0, "skipped": 2, "credentials": 2})                               # the token-named file and the leftover copy; the symlink is simply not a regular file
        copy = sqlite3.connect(entries["db/a.sqlite"])
        self.assertEqual(copy.execute("select count(*) from t").fetchone(), (3,))
        copy.close()
        self.assertNotEqual(entries["db/a.sqlite"], str(self.src / "a.sqlite"))              # a copy, never the live file

    def test_a_secret_folder_is_never_a_way_around_the_rest(self):
        entries, _ = ob.collect(str(self.work), local_run, **self.lists())
        self.assertNotIn("t/my-token.txt", entries)                                          # a token file in an ordinary folder is still skipped

    def test_a_missing_required_source_stops_the_run(self):
        lists = self.lists()
        os.unlink(self.src / "conf.json")
        with self.assertRaises(ob.BackupError) as raised:
            ob.collect(str(self.work), local_run, **lists)
        self.assertEqual(str(raised.exception), "backup_source_missing")

    def test_a_missing_optional_source_is_counted(self):
        lists = self.lists(required=False)
        os.unlink(self.src / "conf.json")
        entries, extra = ob.collect(str(self.work), local_run, **lists)
        self.assertEqual(extra["missing"], 1)
        self.assertNotIn("conf/conf.json", entries)

    def test_a_secret_shaped_list_entry_is_refused_before_anything_is_read(self):
        lists = self.lists()
        lists["files"] = (("auth/auth.toml", str(self.src / "conf.json"), True),)
        with self.assertRaises(ob.BackupError) as raised:
            ob.collect(str(self.work), local_run, **lists)
        self.assertEqual(str(raised.exception), "backup_secret_path")

    def test_a_corrupt_database_is_a_fixed_code(self):
        lists = self.lists()
        (self.src / "a.sqlite").write_bytes(b"not a database" * 100)
        with self.assertRaises(ob.BackupError) as raised:
            ob.collect(str(self.work), local_run, **lists)
        self.assertIn(str(raised.exception), ("backup_db_corrupt", "backup_db_snapshot"))

    def test_a_live_wal_database_is_copied_consistently(self):
        live = make_db(self.src / "live.sqlite", rows=5)                                     # left OPEN, with its -wal in play
        live.execute("insert into t values ('late')")
        live.commit()
        lists = self.lists()
        lists["sqlite"] = (("db/live.sqlite", str(self.src / "live.sqlite"), "me", True),)
        entries, _ = ob.collect(str(self.work), local_run, **lists)
        copy = sqlite3.connect(entries["db/live.sqlite"])
        self.assertEqual(copy.execute("select count(*) from t").fetchone(), (6,))
        copy.close()
        live.close()


class Archive(Tmp):
    def test_the_archive_holds_exactly_the_entries_with_normalised_owner_and_mode(self):
        entries, _ = ob.collect(str(self.work), local_run, **self.lists())
        dest = str(self.work / "x.tar.gz")
        self.assertEqual(ob.build_archive(entries, dest), 6)
        with tarfile.open(dest) as tar:
            self.assertEqual(sorted(tar.getnames()), sorted(entries))
            for member in tar.getmembers():
                self.assertEqual((member.uid, member.gid, member.mode), (0, 0, 0o600))
            self.assertEqual(tar.extractfile("t/keep.md").read(), b"keep")

    def test_an_oversize_archive_is_refused(self):
        entries, _ = ob.collect(str(self.work), local_run, **self.lists())
        with mock.patch.object(ob, "MAX_ARCHIVE", 10), self.assertRaises(ob.BackupError) as raised:
            ob.build_archive(entries, str(self.work / "x.tar.gz"))
        self.assertEqual(str(raised.exception), "backup_too_big")


@unittest.skipUnless(HAVE_GPG, "gpg is not installed")
class Encrypt(Tmp):
    def keys(self):
        home = Path(self.tmp) / "owner"
        home.mkdir(mode=0o700)
        base = ["gpg", "--batch", "--no-tty", "--homedir", str(home)]
        subprocess.run(base + ["--pinentry-mode", "loopback", "--passphrase", "", "--quick-generate-key", "backup@example.com", "default", "default", "never"],
                       check=True, capture_output=True, timeout=120)
        public = Path(self.tmp) / "public.asc"
        secret = Path(self.tmp) / "secret.asc"
        public.write_bytes(subprocess.run(base + ["--armor", "--export", "backup@example.com"], check=True, capture_output=True).stdout)
        secret.write_bytes(subprocess.run(base + ["--pinentry-mode", "loopback", "--passphrase", "", "--armor", "--export-secret-keys", "backup@example.com"],
                                          check=True, capture_output=True).stdout)
        return base, public, secret

    def test_what_is_encrypted_opens_only_with_the_private_key(self):
        base, public, _ = self.keys()
        plain, sealed = self.work / "p.bin", self.work / "p.bin.gpg"
        plain.write_bytes(b"state" * 1000)
        ob.encrypt(str(plain), str(sealed), str(self.work), str(public))
        self.assertNotIn(b"state", sealed.read_bytes())
        opened = subprocess.run(base + ["--pinentry-mode", "loopback", "--passphrase", "", "--decrypt", str(sealed)], capture_output=True, check=True).stdout
        self.assertEqual(opened, b"state" * 1000)
        elsewhere = Path(self.tmp) / "stranger"
        elsewhere.mkdir(mode=0o700)
        self.assertNotEqual(subprocess.run(["gpg", "--batch", "--no-tty", "--homedir", str(elsewhere), "--decrypt", str(sealed)], capture_output=True).returncode, 0)

    def test_a_key_file_that_carries_a_secret_key_is_refused(self):
        _, _, secret = self.keys()
        plain = self.work / "p.bin"
        plain.write_bytes(b"x")
        with self.assertRaises(ob.BackupError) as raised:
            ob.encrypt(str(plain), str(self.work / "o.gpg"), str(self.work), str(secret))
        self.assertEqual(str(raised.exception), "backup_key_has_secret")

    def test_an_unreadable_key_file_is_a_fixed_code(self):
        plain, junk = self.work / "p.bin", self.work / "junk.asc"
        plain.write_bytes(b"x")
        junk.write_text("not a key")
        with self.assertRaises(ob.BackupError) as raised:
            ob.encrypt(str(plain), str(self.work / "o.gpg"), str(self.work), str(junk))
        self.assertEqual(str(raised.exception), "backup_key_unreadable")


class Response:
    def __init__(self, body=b"", headers=None):
        self._body, self.headers = body, headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeDrive:
    """A tiny Drive: records every request; answers the token, folder, resumable upload and list calls."""

    def __init__(self, files=0, scope=ob.SCOPE, md5=None, status=None, folders=0):
        self.requests, self.scope, self.md5, self.status = [], scope, md5, status
        self.folders = [{"id": "folder-1"}] * folders
        self.files = [{"id": f"f{i}", "name": f"n{i}"} for i in range(files)]           # newest first, as the real orderBy returns them
        self.sent, self.fields = b"", ""

    def __call__(self, request, timeout=None):
        method, url = request.get_method(), request.full_url
        self.requests.append((method, url.split("?")[0]))
        if self.status and "oauth2" not in url:
            raise urllib.error.HTTPError(url, self.status, "x", {}, io.BytesIO(b"body with a-secret"))
        if "oauth2" in url:
            return Response(json.dumps({"access_token": "tok-a", "scope": self.scope}).encode())
        if method == "GET" and "mimeType%3D%27application%2Fvnd.google-apps.folder" in url:
            return Response(json.dumps({"files": self.folders}).encode())
        if method == "GET":
            return Response(json.dumps({"files": self.files}).encode())
        if method == "POST" and "uploadType=resumable" in url:
            self.fields = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("fields", [""])[0].split(",")      # Drive answers the final PUT with only these
            return Response(b"{}", {"Location": "https://www.googleapis.com/upload/drive/v3/files?upload_id=u1"})
        if method == "POST":
            return Response(json.dumps({"id": "folder-1"}).encode())
        if method == "PUT":
            import hashlib
            self.sent = request.data
            full = {"id": "new", "md5Checksum": self.md5 or hashlib.md5(request.data).hexdigest(), "size": str(len(request.data))}
            return Response(json.dumps({k: v for k, v in full.items() if k in self.fields}).encode())
        if method == "DELETE":
            return Response(b"")
        raise AssertionError(method + " " + url)


class DriveClient(Tmp):
    def payload(self):
        path = self.work / "sealed.gpg"
        path.write_bytes(b"sealed-bytes")
        return str(path)

    def test_upload_creates_the_folder_once_sends_the_bytes_and_verifies_the_checksum(self):
        fake = FakeDrive()
        drive = ob.Drive(CRED, opener=fake)
        folder = drive.folder()
        drive.upload(folder, "n.gpg", self.payload())
        self.assertEqual(fake.sent, b"sealed-bytes")
        self.assertIn(("POST", ob.API), fake.requests)                                      # the folder was made

    def test_an_existing_folder_is_reused(self):
        fake = FakeDrive(folders=1)
        self.assertEqual(ob.Drive(CRED, opener=fake).folder(), "folder-1")
        self.assertNotIn(("POST", ob.API), fake.requests)

    def test_a_checksum_that_is_not_ours_is_an_unverified_upload(self):
        fake = FakeDrive(md5="0" * 32)
        drive = ob.Drive(CRED, opener=fake)
        with self.assertRaises(ob.BackupError) as raised:
            drive.upload("folder-1", "n.gpg", self.payload())
        self.assertEqual(str(raised.exception), "backup_drive_upload_unverified")

    def test_a_wider_or_missing_scope_is_refused(self):
        for scope in ("https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/drive", ""):
            with self.assertRaises(ob.BackupError) as raised:
                ob.Drive(CRED, opener=FakeDrive(scope=scope)).folder()
            self.assertEqual(str(raised.exception), "backup_drive_scope")

    def test_an_http_error_is_a_fixed_code_with_no_body_or_token(self):
        with self.assertRaises(ob.BackupError) as raised:
            ob.Drive(CRED, opener=FakeDrive(status=403)).folder()
        self.assertEqual(str(raised.exception), "backup_drive_http_403")
        self.assertNotIn("secret", str(raised.exception))

    def test_a_bad_credential_is_a_fixed_code(self):
        for raw in ("not json", "{}", json.dumps({"client_id": "a", "client_secret": "", "refresh_token": "c"})):
            with self.assertRaises(ob.BackupError) as raised:
                ob.Drive(raw)
            self.assertEqual(str(raised.exception), "backup_credential_invalid")

    def test_the_upload_location_must_be_googles(self):
        class Evil(FakeDrive):
            def __call__(self, request, timeout=None):
                if request.get_method() == "POST" and "uploadType=resumable" in request.full_url:
                    return Response(b"{}", {"Location": "https://evil.example/steal"})
                return super().__call__(request, timeout)
        with self.assertRaises(ob.BackupError) as raised:
            ob.Drive(CRED, opener=Evil()).upload("folder-1", "n.gpg", self.payload())
        self.assertEqual(str(raised.exception), "backup_drive_off_contract")


class Retention(unittest.TestCase):
    def test_the_newest_fourteen_stay(self):
        fake = FakeDrive(files=17)
        kept, removed = ob.prune(ob.Drive(CRED, opener=fake), "folder-1")
        self.assertEqual((kept, removed), (14, 3))
        deleted = [u for m, u in fake.requests if m == "DELETE"]
        self.assertEqual([u.rsplit("/", 1)[1] for u in deleted], ["f14", "f15", "f16"])      # the three oldest

    def test_nothing_is_removed_at_or_under_the_limit(self):
        fake = FakeDrive(files=14)
        self.assertEqual(ob.prune(ob.Drive(CRED, opener=fake), "folder-1"), (14, 0))
        self.assertFalse([1 for m, _ in fake.requests if m == "DELETE"])

    def test_a_run_never_removes_more_than_three(self):
        fake = FakeDrive(files=40)
        self.assertEqual(ob.prune(ob.Drive(CRED, opener=fake), "folder-1"), (37, 3))

    def test_only_this_apps_tagged_files_in_its_own_folder_are_listed(self):
        fake = FakeDrive(files=1)
        seen = []
        original = fake.__call__

        def spy(request, timeout=None):
            seen.append(request.full_url)
            return original(request, timeout)
        ob.prune(ob.Drive(CRED, opener=spy), "folder-1")
        listing = [u for u in seen if "files?" in u and "q=" in u][0]
        self.assertIn("in+parents", listing)
        self.assertIn("appProperties+has", listing)


class Run(Tmp):
    def go(self, drive, **over):
        lists = self.lists()
        state = str(Path(self.tmp) / "state")
        patches = [mock.patch.object(ob, "SQLITE", lists["sqlite"]), mock.patch.object(ob, "FILES", lists["files"]), mock.patch.object(ob, "TREES", lists["trees"]), mock.patch.object(ob, "SECRET_TREES", lists["secret_trees"]),
                   mock.patch.object(ob.shutil, "chown", lambda *a, **k: None),
                   mock.patch.object(ob, "encrypt", lambda plain, dest, work, key_file=None, run=None: shutil.copy(plain, dest))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        record = ob.run(CRED, state_dir=state, drive=drive, sh=local_run, clock=lambda: 1793145932, **over)
        return record, state

    def test_a_good_night_writes_an_ok_status_with_counts_only(self):
        fake = FakeDrive(files=15, folders=1)
        record, state = self.go(ob.Drive(CRED, opener=fake))
        self.assertEqual((record["ok"], record["code"], record["files"], record["kept"], record["removed"]), (True, "ok", 6, 14, 1))
        text = (Path(state) / "status.json").read_text()
        self.assertEqual(json.loads(text), record)
        for private in (self.tmp, "keep.md", "a.sqlite", "not-a-real"):
            self.assertNotIn(private, text)
        self.assertEqual(os.listdir(state), ["status.json"])                                 # the work folder, the snapshots and the plain archive are all gone

    def test_the_plain_archive_is_deleted_before_the_upload_starts(self):
        order = []

        class Watching(FakeDrive):
            def __call__(inner, request, timeout=None):
                if request.get_method() == "PUT":
                    order.append(sorted(f for _, _, fs in os.walk(os.path.join(self.tmp, "state")) for f in fs))
                return super().__call__(request, timeout)
        self.go(ob.Drive(CRED, opener=Watching(folders=1)))
        self.assertNotIn("state.tar.gz", order[0])
        self.assertIn("state.tar.gz.gpg", order[0])

    def test_a_failed_upload_is_a_failed_status_and_nothing_is_pruned(self):
        fake = FakeDrive(files=30, folders=1, md5="0" * 32)
        record, _ = self.go(ob.Drive(CRED, opener=fake))
        self.assertEqual((record["ok"], record["code"]), (False, "backup_drive_upload_unverified"))
        self.assertFalse([1 for m, _ in fake.requests if m == "DELETE"])

    def test_dry_stops_before_drive_and_writes_no_status(self):
        fake = FakeDrive(folders=1)
        record, state = self.go(ob.Drive(CRED, opener=fake), dry=True)
        self.assertEqual((record["ok"], record["code"], record["files"]), (True, "dry_ok", 6))
        self.assertEqual(fake.requests, [])
        self.assertFalse((Path(state) / "status.json").exists())

    def test_check_reports_never_failed_stale_and_ok(self):
        state = str(Path(self.tmp) / "state")
        self.assertEqual(ob.check(state), (False, "backup_never_ran"))
        ob.write_status(state, False, "backup_drive_http_403", lambda: 1000)
        self.assertEqual(ob.check(state, lambda: 1001), (False, "backup_drive_http_403"))
        ob.write_status(state, True, "ok", lambda: 1000, files=3)
        self.assertEqual(ob.check(state, lambda: 1000 + 3600), (True, "ok"))
        self.assertEqual(ob.check(state, lambda: 1000 + ob.STALE_SECONDS + 1), (False, "backup_stale"))


class Consent(unittest.TestCase):
    def test_the_sign_in_asks_for_drive_file_only(self):
        url = consent.auth_url("id.example", "http://127.0.0.1:1", "s", "v")
        self.assertIn("auth%2Fdrive.file", url)
        self.assertNotIn("auth%2Fdrive&", url + "&")

    def test_a_wider_grant_or_a_missing_refresh_token_stores_nothing(self):
        def opener(scope, refresh="r"):
            return lambda request, timeout=None: Response(json.dumps({"scope": scope, "refresh_token": refresh}).encode())
        good = consent.exchange("id.example", "s", "c", "http://127.0.0.1:1", "v", opener(consent.SCOPE))
        self.assertEqual(json.loads(good), {"client_id": "id.example", "client_secret": "s", "refresh_token": "r"})
        for scope in ("https://www.googleapis.com/auth/drive", consent.SCOPE + " https://www.googleapis.com/auth/drive", ""):
            with self.assertRaises(SystemExit):
                consent.exchange("id.example", "s", "c", "http://127.0.0.1:1", "v", opener(scope))
        with self.assertRaises(SystemExit):
            consent.exchange("id.example", "s", "c", "http://127.0.0.1:1", "v", opener(consent.SCOPE, refresh=""))

    def test_it_refuses_to_print_the_credential_on_a_terminal(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        tty = io.StringIO()
        self.assertEqual(consent.main([], stdout=Terminal(), tty=tty), 2)
        self.assertIn("Refused", tty.getvalue())


class Installer(unittest.TestCase):
    def test_the_shell_script_parses(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(HERE / "oracle" / "install-offsite.sh")], capture_output=True).returncode, 0)

    def test_the_installer_touches_only_its_own_things(self):
        code = "\n".join(l for l in (HERE / "oracle" / "install-offsite.sh").read_text().splitlines() if not l.lstrip().startswith("#"))
        for word in ("tailscale", "47443", "lifeos-close", "openclaw-gateway", "systemctl restart", "systemctl stop"):
            self.assertNotIn(word, code)

    def test_the_service_is_a_hardened_oneshot_with_an_encrypted_credential_and_no_listener(self):
        text = (HERE / "oracle" / "lifeos-offsite-backup.service").read_text()
        for line in ("Type=oneshot", "LoadCredentialEncrypted=drive:/etc/credstore.encrypted/lifeos-offsite.drive", "NoNewPrivileges=yes", "PrivateTmp=yes"):
            self.assertIn(line, text)
        self.assertNotIn("Listen", text)

    def test_the_timer_runs_nightly_in_central_time_and_catches_up(self):
        text = (HERE / "oracle" / "lifeos-offsite-backup.timer").read_text()
        self.assertIn("OnCalendar=*-*-* 02:30:00 America/Chicago", text)
        self.assertIn("Persistent=true", text)

    def test_the_module_holds_no_secret_shaped_literal_and_stays_stdlib(self):
        source = (HERE / "offsite_backup.py").read_text()
        for word in ("import requests", "import google", "BEGIN PRIVATE KEY"):
            self.assertNotIn(word, source)


if __name__ == "__main__":
    unittest.main()
