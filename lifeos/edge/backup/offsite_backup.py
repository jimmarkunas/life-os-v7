"""LIFE-129 (Jim, Oct 27 2026): the nightly off-site backup of the Oracle state that cannot be rebuilt, run as root by systemd on the Edge. Boot-volume backups stay in
one region; this copy goes to Jim's Google Drive, encrypted with a PUBLIC key before it leaves the machine (the private key is never on Oracle: Jim keeps it).
  run     snapshot -> tar.gz -> gpg -> Drive (own folder, own credential, `drive.file` scope only) -> checksum check -> keep the newest 14 -> status file
  check   exit 0 only when the last run succeeded recently (a missed night is a failure, not silence)
What goes in is an explicit list (SQLITE, FILES, TREES): databases (online snapshot, never a raw copy of a live file) and app config. Credential, token and key material
is NEVER collected (DENY): it can be re-issued, and copying it off the machine only widens what a stolen copy could do. Stdlib only. Every error is a fixed code; the
status file and the output hold counts and codes, never a path, a name, a token or a response body. The Drive credential is one JSON object
{"client_id", "client_secret", "refresh_token"} (systemd credential `drive`)."""
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

SCOPE = "https://www.googleapis.com/auth/drive.file"                  # only files this app creates; it can never see the rest of the Drive
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_NAME, FOLDER_TAG, FILE_TAG = "LIFE OS Backups", "offsite-backup-folder", "offsite-backup"
FOLDER_MIME = "application/vnd.google-apps.folder"
KEEP = 14                                                              # nightly copies kept
MAX_DELETE = 3                                                         # never more than this many removals in one run (a bug cannot empty the folder)
MAX_ARCHIVE = 200 * 1024 * 1024
STALE_SECONDS = 36 * 3600                                              # `check`: a night and a half
STATE = "/var/lib/lifeos-offsite"
KEY_FILE = "/etc/lifeos-offsite/recipient.asc"
CENTRAL = ZoneInfo("America/Chicago")

UBUNTU, GRANS = "/home/ubuntu", "/home/grans"
# (archive name, source path, owner, required): databases are snapshotted by their OWNER through the sqlite backup API, so no file on the host changes hands.
SQLITE = (
    ("grans/grans.db", GRANS + "/.local/share/grans/grans.db", "grans", True),
    ("openclaw/state/openclaw.sqlite", UBUNTU + "/.openclaw/state/openclaw.sqlite", "ubuntu", True),
    ("openclaw/state/openclaw-quarantine.sqlite", UBUNTU + "/.openclaw/state/openclaw-quarantine.sqlite", "ubuntu", False),
    ("openclaw/agents/main/openclaw-agent.sqlite", UBUNTU + "/.openclaw/agents/main/agent/openclaw-agent.sqlite", "ubuntu", True),
    ("openclaw/agents/fit/openclaw-agent.sqlite", UBUNTU + "/.openclaw/agents/fit/agent/openclaw-agent.sqlite", "ubuntu", False),
    ("openclaw/agents/fit2/openclaw-agent.sqlite", UBUNTU + "/.openclaw/agents/fit2/agent/openclaw-agent.sqlite", "ubuntu", False),
    ("openclaw/agents/fit3/openclaw-agent.sqlite", UBUNTU + "/.openclaw/agents/fit3/agent/openclaw-agent.sqlite", "ubuntu", False),
)
FILES = (   # (archive name, source path, required)
    ("openclaw/openclaw.json", UBUNTU + "/.openclaw/openclaw.json", True),
    ("close/policy.json", "/etc/lifeos-close/policy.json", False),
    ("systemd-user/lifeos-imessage-alerts@.service", UBUNTU + "/.config/systemd/user/lifeos-imessage-alerts@.service", False),
)
TREES = (   # (archive prefix, source directory): every regular file inside, except the DENY names and old *.bak copies
    ("config/lifeos-fit", UBUNTU + "/.config/lifeos-fit"),
    ("config/lifeos-resume", UBUNTU + "/.config/lifeos-resume"),
    ("config/lifeos-alerts", UBUNTU + "/.config/lifeos-alerts"),
    ("openclaw/workspace", UBUNTU + "/.openclaw/workspace"),
    ("openclaw/jira-approvals", UBUNTU + "/.openclaw/jira-approvals"),
)
DENY = re.compile(r"credential|secret|token|passw|\.env\b|auth|key|bws|/gh/|notion|\.pem|id_rsa|\.bak", re.I)    # never collected, and a test holds every list above clear of it


class BackupError(RuntimeError):
    """The message is always a fixed code."""


def now_central(clock=time.time):
    """A file name in Central time, 12-hour (Jim reads these names)."""
    return datetime.datetime.fromtimestamp(clock(), CENTRAL).strftime("%Y-%m-%d_%I%M%p_CT")


def denied(*names):
    return any(DENY.search(n) for n in names)


# --- collect -------------------------------------------------------------------------------------------------------------------------------------------------------

_SNAP = ("import sqlite3, sys\n"
         "src = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True, timeout=30)\n"
         "dst = sqlite3.connect(sys.argv[2])\n"
         "src.backup(dst)\n"
         "ok = dst.execute('pragma integrity_check').fetchone() == ('ok',)\n"
         "dst.close(); src.close()\n"
         "sys.exit(0 if ok else 3)\n")


def snapshot(source, dest, owner, run=subprocess.run):
    """One database -> a consistent copy, made BY ITS OWNER (so the owner's own -wal/-shm files are never created or changed by root). Code, never a path."""
    done = run(["runuser", "-u", owner, "--", "python3", "-I", "-c", _SNAP, source, dest], capture_output=True, timeout=300)
    if done.returncode == 3:
        raise BackupError("backup_db_corrupt")
    if done.returncode != 0:
        raise BackupError("backup_db_snapshot")


def collect(work, run=subprocess.run, sqlite=None, files=None, trees=None, chown=None):
    """Everything on the list -> {archive name: file in `work`}, plus counts. A missing REQUIRED source stops the run (nothing is uploaded); a missing optional one is counted."""
    sqlite, files, trees, chown = SQLITE if sqlite is None else sqlite, FILES if files is None else files, TREES if trees is None else trees, chown or shutil.chown
    out, missing, skipped = {}, 0, 0
    for name, source, owner, required in sqlite:
        if denied(name, source):
            raise BackupError("backup_secret_path")
        if not os.path.isfile(source):
            if required:
                raise BackupError("backup_source_missing")
            missing += 1
            continue
        holder = os.path.join(work, "snap-" + owner)
        os.makedirs(holder, mode=0o700, exist_ok=True)
        chown(holder, user=owner)
        dest = os.path.join(holder, hashlib.sha256(name.encode()).hexdigest()[:16] + ".sqlite")
        snapshot(source, dest, owner, run)
        out[name] = dest
    for name, source, required in files:
        if denied(name, source):
            raise BackupError("backup_secret_path")
        if not os.path.isfile(source):
            if required:
                raise BackupError("backup_source_missing")
            missing += 1
            continue
        out[name] = source
    for prefix, root in trees:
        if denied(prefix, root):
            raise BackupError("backup_secret_path")
        for folder, _, names in os.walk(root):
            for leaf in sorted(names):
                path = os.path.join(folder, leaf)
                relative = prefix + "/" + os.path.relpath(path, root)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                if denied(relative, path):
                    skipped += 1
                    continue
                out[relative] = path
    if not out:
        raise BackupError("backup_nothing_to_save")
    return out, {"missing": missing, "skipped": skipped}


def build_archive(entries, dest):
    """{name: file} -> one tar.gz, owner and mode normalised. Returns the number of files."""
    with tarfile.open(dest, "w:gz") as tar:
        for name in sorted(entries):
            info = tar.gettarinfo(entries[name], arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o600
            with open(entries[name], "rb") as handle:
                tar.addfile(info, handle)
    if os.path.getsize(dest) > MAX_ARCHIVE:
        raise BackupError("backup_too_big")
    return len(entries)


# --- encrypt -------------------------------------------------------------------------------------------------------------------------------------------------------

def encrypt(plain, dest, work, key_file=KEY_FILE, run=subprocess.run):
    """Encrypt to the PUBLIC key in `key_file`. Refuses a key file that carries a secret key (Jim's private key must never be on this machine)."""
    home = os.path.join(work, "gnupg")
    os.makedirs(home, mode=0o700, exist_ok=True)
    base = ["gpg", "--batch", "--no-tty", "--homedir", home]
    if run(base + ["--import", key_file], capture_output=True, timeout=60).returncode != 0:
        raise BackupError("backup_key_unreadable")
    if run(base + ["--list-secret-keys", "--with-colons"], capture_output=True, timeout=60).stdout.strip():
        raise BackupError("backup_key_has_secret")
    listing = run(base + ["--list-keys", "--with-colons"], capture_output=True, text=True, timeout=60).stdout
    fingerprints = [line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")]
    if len(fingerprints) < 1:
        raise BackupError("backup_key_unreadable")
    command = base + ["--yes", "--trust-model", "always", "--output", dest, "--encrypt"]
    for fingerprint in fingerprints[:1]:
        command += ["--recipient", fingerprint]
    if run(command + [plain], capture_output=True, timeout=300).returncode != 0 or not os.path.getsize(dest):
        raise BackupError("backup_encrypt")


# --- Drive ---------------------------------------------------------------------------------------------------------------------------------------------------------

def credential(raw):
    try:
        info = json.loads(raw)
        out = tuple(info[k] for k in ("client_id", "client_secret", "refresh_token"))
    except (ValueError, KeyError, TypeError):
        raise BackupError("backup_credential_invalid") from None
    if not all(isinstance(v, str) and v for v in out):
        raise BackupError("backup_credential_invalid")
    return out


def _q(value):
    return value.replace("\\", "\\\\").replace("'", "\\'")


class Drive:
    def __init__(self, raw, opener=urllib.request.urlopen, timeout=60):
        self._creds, self._open, self._timeout, self._token = credential(raw), opener, timeout, None

    def _call(self, method, url, data=None, content_type=None, auth=True, extra=None, want_headers=False):
        headers = dict(extra or {})
        if content_type:
            headers["Content-Type"] = content_type
        if auth:
            headers["Authorization"] = "Bearer " + self._access_token()
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._open(request, timeout=self._timeout) as response:
                raw = response.read()
                found = response.headers
            body = json.loads(raw) if raw else {}
            return (body, found) if want_headers else body
        except urllib.error.HTTPError as error:
            raise BackupError(f"backup_drive_http_{error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise BackupError("backup_drive_network") from None

    def _access_token(self):
        if self._token is None:
            client_id, client_secret, refresh = self._creds
            reply = self._call("POST", TOKEN_URL, urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh,
                                                                          "grant_type": "refresh_token"}).encode(), "application/x-www-form-urlencoded", auth=False)
            token = reply.get("access_token")
            if not isinstance(token, str) or not token:
                raise BackupError("backup_drive_token_refused")
            if SCOPE not in str(reply.get("scope", "")).split() or "https://www.googleapis.com/auth/drive" in str(reply.get("scope", "")).split():
                raise BackupError("backup_drive_scope")                  # exactly drive.file: a wider grant is refused, not used
            self._token = token
        return self._token

    def _find(self, query, order="createdTime desc"):
        reply = self._call("GET", API + "?" + urllib.parse.urlencode({"q": query + " and trashed=false", "spaces": "drive", "orderBy": order, "pageSize": 1000,
                                                                       "fields": "files(id,name,createdTime,md5Checksum,size)"}))
        files = reply.get("files")
        if not isinstance(files, list):
            raise BackupError("backup_drive_off_contract")
        return files

    def folder(self):
        """The one backups folder this app made (oldest if there were ever two), created once at the top of My Drive."""
        found = self._find(f"mimeType='{FOLDER_MIME}' and name='{_q(FOLDER_NAME)}' and appProperties has {{ key='lifeos' and value='{FOLDER_TAG}' }}", "createdTime")
        if found:
            return found[0]["id"]
        made = self._call("POST", API + "?fields=id", json.dumps({"name": FOLDER_NAME, "mimeType": FOLDER_MIME, "appProperties": {"lifeos": FOLDER_TAG}}).encode(),
                          "application/json")
        if not isinstance(made.get("id"), str):
            raise BackupError("backup_drive_off_contract")
        return made["id"]

    def upload(self, folder_id, name, path):
        """One file, resumable upload -> its metadata, checked: the checksum Drive reports must be the bytes we hold."""
        with open(path, "rb") as handle:
            data = handle.read()
        meta = json.dumps({"name": name, "parents": [folder_id], "mimeType": "application/octet-stream", "appProperties": {"lifeos": FILE_TAG}}).encode()
        _, headers = self._call("POST", UPLOAD + "?uploadType=resumable&fields=id", meta, "application/json; charset=UTF-8", want_headers=True,
                                extra={"X-Upload-Content-Type": "application/octet-stream", "X-Upload-Content-Length": str(len(data))})
        location = headers.get("Location")
        if not location or not location.startswith("https://www.googleapis.com/"):
            raise BackupError("backup_drive_off_contract")
        made = self._call("PUT", location + ("&" if "?" in location else "?") + "fields=id,md5Checksum,size", data, "application/octet-stream")
        if made.get("md5Checksum") != hashlib.md5(data).hexdigest() or str(made.get("size")) != str(len(data)):
            raise BackupError("backup_drive_upload_unverified")
        return made

    def kept(self, folder_id):
        return self._find(f"'{_q(folder_id)}' in parents and appProperties has {{ key='lifeos' and value='{FILE_TAG}' }}")

    def remove(self, file_id):
        self._call("DELETE", f"{API}/{urllib.parse.quote(file_id, safe='')}")


def prune(drive, folder_id, keep=KEEP, limit=MAX_DELETE):
    """Newest `keep` stay. Only this app's own tagged files in its own folder are ever listed; at most `limit` are removed per run. -> (kept, removed)."""
    files = drive.kept(folder_id)
    extra = files[keep:]
    removed = 0
    for old in extra[:limit]:
        drive.remove(old["id"])
        removed += 1
    return len(files) - removed, removed


# --- run / status --------------------------------------------------------------------------------------------------------------------------------------------------

def write_status(state_dir, ok, code, clock=time.time, **counts):
    os.makedirs(state_dir, mode=0o755, exist_ok=True)
    record = {"ok": bool(ok), "code": code, "at": int(clock())}
    record.update({k: int(v) for k, v in counts.items()})
    tmp = os.path.join(state_dir, "status.json.new")
    with open(tmp, "w") as handle:
        json.dump(record, handle)
    os.replace(tmp, os.path.join(state_dir, "status.json"))
    return record


def run(raw_credential, state_dir=STATE, key_file=KEY_FILE, drive=None, sh=subprocess.run, clock=time.time, dry=False):
    """One night's backup. -> the status record (also written to the state dir). Never raises; a failure is the record's `code`.
    `dry`: stop after the encrypted copy exists (no Drive, no status file written): the proof the snapshots and the key work before any credential is installed."""
    work = snaps = None
    counts = {}
    write = (lambda *a, **k: {"ok": a[1], "code": a[2], **{x: int(y) for x, y in k.items()}}) if dry else write_status
    try:
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        work = tempfile.mkdtemp(prefix="work-", dir=state_dir)
        os.chmod(work, 0o700)
        snaps = tempfile.mkdtemp(prefix="offsite-snap-", dir="/var/tmp")                # traversable by the database owners (they write their own snapshot here); nothing else lives in it
        os.chmod(snaps, 0o711)
        entries, extra = collect(snaps, sh)
        counts.update(extra, files=len(entries))
        archive = os.path.join(work, "state.tar.gz")
        build_archive(entries, archive)
        sealed = os.path.join(work, "state.tar.gz.gpg")
        encrypt(archive, sealed, work, key_file, sh)
        counts["bytes"] = os.path.getsize(sealed)
        os.unlink(archive)                                                # the plain copy goes first
        shutil.rmtree(snaps, ignore_errors=True)
        if dry:
            return write(state_dir, True, "dry_ok", clock, **counts)
        drive = drive or Drive(raw_credential)
        folder_id = drive.folder()
        drive.upload(folder_id, "lifeos-edge-" + now_central(clock) + ".tar.gz.gpg", sealed)
        remaining, removed = prune(drive, folder_id)
        counts.update(kept=remaining, removed=removed)
        return write(state_dir, True, "ok", clock, **counts)
    except BackupError as error:
        return write(state_dir, False, str(error), clock, **counts)
    except (OSError, subprocess.SubprocessError, tarfile.TarError, sqlite3.Error):
        return write(state_dir, False, "backup_unexpected", clock, **counts)
    finally:
        for folder in (work, snaps):
            if folder:
                shutil.rmtree(folder, ignore_errors=True)


def check(state_dir=STATE, clock=time.time, limit=STALE_SECONDS):
    """-> (ok, code): the last run succeeded and is recent."""
    try:
        with open(os.path.join(state_dir, "status.json")) as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return False, "backup_never_ran"
    if not record.get("ok"):
        return False, str(record.get("code", "backup_failed"))
    if clock() - int(record.get("at", 0)) > limit:
        return False, "backup_stale"
    return True, "ok"


def main(argv=None, environ=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if environ is None else environ
    if argv[:1] == ["check"]:
        ok, code = check()
        print(code)
        return 0 if ok else 1
    if argv[:1] == ["dry"]:
        scratch = tempfile.mkdtemp(prefix="dry-", dir="/var/tmp")
        try:
            record = run("", state_dir=scratch, dry=True)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        print(record["code"], "files=%s bytes=%s missing=%s skipped=%s" % tuple(record.get(k, 0) for k in ("files", "bytes", "missing", "skipped")))
        return 0 if record["ok"] else 1
    if argv[:1] != ["run"]:
        print("usage: offsite_backup.py run|check|dry")
        return 2
    directory = environ.get("CREDENTIALS_DIRECTORY", "")
    try:
        with open(os.path.join(directory, "drive")) as handle:
            raw = handle.read()
    except OSError:
        print(write_status(STATE, False, "backup_credential_missing")["code"])
        return 1
    record = run(raw)
    print(record["code"], "files=%s bytes=%s kept=%s removed=%s" % tuple(record.get(k, 0) for k in ("files", "bytes", "kept", "removed")))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
