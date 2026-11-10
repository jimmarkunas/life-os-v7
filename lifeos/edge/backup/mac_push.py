"""LIFE-538: back up the Mac-only Hermes and OpenClaw data. Runs on Jim's MacBook Pro, never on Oracle.

  python -m lifeos.edge.backup.mac_push            dry run: build the archive and encrypt it, send nothing, keep nothing
  python -m lifeos.edge.backup.mac_push --push     the same, then send it to the Oracle inbox over SFTP (key-only, one folder) and keep the newest 3 there

What goes in is an explicit list (SQLITE, FILES, TREES). Databases are copied with the sqlite online-backup API (never a raw copy of a live file). Credential-shaped files are
included on purpose (Jim's decision, Nov 9 2026: a lost Mac must not mean re-linking everything) and that is safe ONLY because the archive is encrypted to a PUBLIC key before it
leaves the temporary folder; the private key is never on this machine. The plain archive is deleted right after encryption. Output is counts and fixed codes only: no file name
from the secret list, no value. Stdlib plus the `gpg` and `sftp` programs.
"""
import datetime
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from zoneinfo import ZoneInfo

HOME = os.path.expanduser("~")
KEY_FILE = os.path.join(HOME, ".config/life-os/offsite-recipient.asc")        # the PUBLIC key the Oracle off-site job already uses; Jim holds the private half
SSH_KEY = os.path.join(HOME, ".ssh/lifeos-macinbox")                           # dedicated, SFTP-only into one Oracle folder
REMOTE = "macinbox@lifeos-edge-01"
KEEP = 3
MAX_ARCHIVE = 100 * 1024 * 1024
CENTRAL = ZoneInfo("America/Chicago")
NAME = re.compile(r"^mac-\d{8}-\d{4}\.tar\.gz\.gpg$")

# (archive name, path under HOME): copied with the sqlite backup API. A missing one is counted, not fatal (a service may be off).
SQLITE = (
    ("hermes/state.db", ".hermes/state.db"),
    ("hermes/kanban.db", ".hermes/kanban.db"),
    ("hermes/projects.db", ".hermes/projects.db"),
    ("hermes/cron/executions.db", ".hermes/cron/executions.db"),
    ("openclaw/state/openclaw.sqlite", ".openclaw/state/openclaw.sqlite"),
    ("openclaw/state/openclaw-quarantine.sqlite", ".openclaw/state/openclaw-quarantine.sqlite"),
    ("openclaw/agents/main/openclaw-agent.sqlite", ".openclaw/agents/main/agent/openclaw-agent.sqlite"),
)
FILES = (
    ("hermes/config.yaml", ".hermes/config.yaml"),
    ("hermes/SOUL.md", ".hermes/SOUL.md"),
    ("hermes/auth.json", ".hermes/auth.json"),                                  # secret: only inside the encrypted archive
    ("openclaw/openclaw.json", ".openclaw/openclaw.json"),
    ("openclaw/mac-control.token", ".openclaw/mac-control.token"),              # secret
    ("openclaw/config-journal-fingerprint.key", ".openclaw/config-journal-fingerprint.key"),   # secret
    ("config/life-os/lifeos-bws-run", ".config/life-os/lifeos-bws-run"),
    ("config/life-os/newsletter-policy.json", ".config/life-os/newsletter-policy.json"),
)
TREES = (   # (archive prefix, path under HOME): every regular file inside, except live-database side files and lock files
    ("hermes/memories", ".hermes/memories"),
    ("hermes/plugins", ".hermes/plugins"),
    ("hermes/cron", ".hermes/cron"),
    ("openclaw/credentials", ".openclaw/credentials"),                          # secret
    ("openclaw/service-env", ".openclaw/service-env"),                          # secret
    ("openclaw/workspace", ".openclaw/workspace"),
)
SKIP_LEAF = re.compile(r"(-shm|-wal|\.lock|\.sock|\.pid|\.db|\.sqlite)$|\.reindex-lock\.sqlite$")   # databases are taken whole by the backup API above, not by file walk
NEVER_FOLDERS = ("hermes-agent", "tools", "installs", "cache", "logs", "node_modules", "__pycache__")   # re-downloadable or noise; not in any list, listed here so a test can hold it


class PushError(Exception):
    """The message is always a fixed code."""


def stamp(clock=time.time):
    return datetime.datetime.fromtimestamp(clock(), CENTRAL).strftime("%Y%m%d-%H%M")


def snapshot(source, dest):
    """Online backup of a live SQLite file -> a consistent copy; refuses a database that fails an integrity check."""
    src = sqlite3.connect("file:%s?mode=ro" % source, uri=True, timeout=30)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
            if dst.execute("pragma integrity_check").fetchone()[0] != "ok":
                raise PushError("mac_backup_db_corrupt")
        finally:
            dst.close()
    except sqlite3.Error:
        raise PushError("mac_backup_db_unreadable")
    finally:
        src.close()


def collect(work, home=HOME, sqlite=None, files=None, trees=None):
    """-> ({archive name: file in `work` or on disk}, counts). Nothing is read from outside the three lists."""
    sqlite, files, trees = SQLITE if sqlite is None else sqlite, FILES if files is None else files, TREES if trees is None else trees
    out, missing = {}, 0
    for name, rel in sqlite:
        source = os.path.join(home, rel)
        if not os.path.isfile(source):
            missing += 1
            continue
        dest = os.path.join(work, hashlib.sha256(name.encode()).hexdigest()[:16] + ".sqlite")
        snapshot(source, dest)
        out[name] = dest
    for name, rel in files:
        source = os.path.join(home, rel)
        if os.path.islink(source) or not os.path.isfile(source):
            missing += 1
            continue
        out[name] = source
    for prefix, rel in trees:
        root = os.path.join(home, rel)
        if not os.path.isdir(root):
            missing += 1
            continue
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in NEVER_FOLDERS]
            for leaf in sorted(names):
                path = os.path.join(folder, leaf)
                if os.path.islink(path) or not os.path.isfile(path) or SKIP_LEAF.search(leaf):
                    continue
                out[prefix + "/" + os.path.relpath(path, root)] = path
    if not out:
        raise PushError("mac_backup_nothing_to_save")
    return out, {"missing": missing}


def build_archive(entries, dest):
    with tarfile.open(dest, "w:gz") as tar:
        for name in sorted(entries):
            info = tar.gettarinfo(entries[name], arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o600
            with open(entries[name], "rb") as handle:
                tar.addfile(info, handle)
    if os.path.getsize(dest) > MAX_ARCHIVE:
        raise PushError("mac_backup_too_big")
    return len(entries)


def encrypt(plain, dest, work, key_file=KEY_FILE, run=subprocess.run):
    """Encrypt to the PUBLIC key in `key_file`. Refuses a key file that carries a secret key."""
    home = os.path.join(work, "gnupg")
    os.makedirs(home, mode=0o700, exist_ok=True)
    base = ["gpg", "--batch", "--no-tty", "--homedir", home]
    if run(base + ["--import", key_file], capture_output=True, timeout=60).returncode != 0:
        raise PushError("mac_backup_key_unreadable")
    if run(base + ["--list-secret-keys", "--with-colons"], capture_output=True, timeout=60).stdout.strip():
        raise PushError("mac_backup_key_has_secret")
    listing = run(base + ["--list-keys", "--with-colons"], capture_output=True, text=True, timeout=60).stdout
    fingerprints = [line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")]
    if not fingerprints:
        raise PushError("mac_backup_key_unreadable")
    command = base + ["--yes", "--trust-model", "always", "--output", dest, "--encrypt", "--recipient", fingerprints[0], plain]
    if run(command, capture_output=True, timeout=300).returncode != 0 or not os.path.isfile(dest):
        raise PushError("mac_backup_encrypt")
    return fingerprints[0]


def sftp(batch, run=subprocess.run, ssh_key=SSH_KEY, remote=REMOTE):
    command = ["sftp", "-i", ssh_key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-b", "-", remote]
    done = run(command, input=batch, capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise PushError("mac_backup_push_failed")
    return done.stdout


def push(sealed, run=subprocess.run, ssh_key=SSH_KEY, remote=REMOTE):
    name = os.path.basename(sealed)
    if not NAME.match(name):
        raise PushError("mac_backup_bad_name")
    sftp("cd in\nput %s %s.part\nrename %s.part %s\n" % (sealed, name, name, name), run, ssh_key, remote)       # lands under a temporary name, then appears whole
    listing = sftp("cd in\nls -1\n", run, ssh_key, remote)
    names = sorted(line.strip().split("/")[-1] for line in listing.splitlines() if NAME.match(line.strip().split("/")[-1]))
    if name not in names:
        raise PushError("mac_backup_push_unverified")
    stale = names[:-KEEP] if len(names) > KEEP else []
    if stale:
        sftp("cd in\n" + "".join("rm %s\n" % n for n in stale), run, ssh_key, remote)
    return {"remote_kept": min(len(names), KEEP), "remote_removed": len(stale)}


def main(argv=None, home=HOME, key_file=KEY_FILE, run=subprocess.run, clock=time.time, out=sys.stdout):
    argv = sys.argv[1:] if argv is None else argv
    live = "--push" in argv
    work = None
    try:
        work = tempfile.mkdtemp(prefix="mac-push-", dir=os.environ.get("MAC_PUSH_TMP", "/tmp"))
        os.chmod(work, 0o700)
        entries, extra = collect(work, home)
        plain = os.path.join(work, "plain.tar.gz")
        count = build_archive(entries, plain)
        sealed = os.path.join(work, "mac-%s.tar.gz.gpg" % stamp(clock))
        fingerprint = encrypt(plain, sealed, work, key_file, run)
        os.remove(plain)                                                                                           # the plain archive is gone before anything is sent
        result = {"files": count, "bytes": os.path.getsize(sealed), "recipient": fingerprint[-8:], **extra}
        if live:
            result.update(push(sealed, run))
        print("mac_backup ok %s %s" % ("pushed" if live else "dry", " ".join("%s=%s" % kv for kv in sorted(result.items()))), file=out)
        return 0
    except PushError as error:
        print("mac_backup failed %s" % error, file=out)
        return 2
    except Exception:                                                                                              # any other failure is a fixed code, never a message that could carry a name or value
        print("mac_backup failed mac_backup_unexpected", file=out)
        return 2
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
