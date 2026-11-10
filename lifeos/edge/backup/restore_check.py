"""LIFE-538 / LIFE-529: ONE command that proves a real backup can be restored, from ANY computer that can reach Bitwarden. Nothing here depends on the Mac.

  python3 -m lifeos.edge.backup.restore_check ARCHIVE.gpg                              the secret id is read from ~/.config/life-os/offsite-key-secret-id (one line, mode 600)
  python3 -m lifeos.edge.backup.restore_check ARCHIVE.gpg --bws-secret SECRET_ID      the private key lives in Bitwarden Secrets Manager (needs `bws` and its access token)
  python3 -m lifeos.edge.backup.restore_check ARCHIVE.gpg --passphrase-prompt         the key's passphrase is typed by the person at the terminal (hidden, never stored)
  python3 -m lifeos.edge.backup.restore_check ARCHIVE.gpg --bw-item "ITEM NAME"       the private key lives in the Bitwarden vault (needs `bw`, unlocked: BW_SESSION)

ARCHIVE.gpg is either the nightly off-site file downloaded from Drive (lifeos-edge-YYYY....tar.gz.gpg) or the Mac file (mac-YYYYMMDD-HHMM.tar.gz.gpg).

What it does, in order: makes a private RAM-backed folder (Linux /dev/shm, or a macOS RAM disk), reads the private key from Bitwarden straight into gpg's keyring INSIDE that folder
(never a file on disk, never printed, never an argument or environment value), decrypts the archive there, opens the Mac archive inside the nightly one when present (decrypts it too),
checks every member name, runs `PRAGMA integrity_check` on every database, and prints PASS or FAIL with COUNTS ONLY: no file name from the archive, no table name, no content, no key.
Then it deletes the folder and the RAM disk (also when it fails). Stdlib plus `gpg` and `bws` or `bw`. Exit 0 only on PASS.

Fixed codes: restore_key_source (no way to read the key), restore_key_fetch, restore_key_import, restore_no_ram (no RAM-backed folder can be made; nothing is written to disk instead),
restore_decrypt (the key does not open this archive), plus the verify_restore codes (RESTORE_UNSAFE_PATH, RESTORE_TOO_BIG, RESTORE_DB_CORRUPT, RESTORE_DB_UNCHECKED, RESTORE_UNREADABLE).
"""
import contextlib
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

from lifeos.edge.backup import verify_restore as vr

ID_FILE = os.path.expanduser("~/.config/life-os/offsite-key-secret-id")        # one line: the Bitwarden Secrets Manager id of the backup key, kept only on the machine that runs the check (never in Jira or the repo)
MAC_INNER = re.compile(r"^mac/mac-\d{8}-\d{4}\.tar\.gz\.gpg$")
RAM_MB = 512


class CheckError(RuntimeError):
    """The message is always a fixed code."""


@contextlib.contextmanager
def ram_folder(run=subprocess.run, platform=sys.platform, mb=RAM_MB):
    """A private folder that lives in memory only. Linux: /dev/shm. macOS: a RAM disk (hdiutil, no admin needed), detached afterwards. Anything else: refused (never falls back to disk)."""
    if platform.startswith("linux") and os.path.isdir("/dev/shm"):
        path = tempfile.mkdtemp(prefix="rc-", dir="/dev/shm")
        os.chmod(path, 0o700)
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)
        return
    if platform == "darwin":
        attach = run(["hdiutil", "attach", "-nomount", "ram://%d" % (mb * 2048)], capture_output=True, text=True, timeout=60)
        device = (attach.stdout.split() or [""])[0]
        if attach.returncode != 0 or not device.startswith("/dev/"):
            raise CheckError("restore_no_ram")
        try:
            name = "rc" + uuid.uuid4().hex[:8]
            if run(["diskutil", "erasevolume", "HFS+", name, device], capture_output=True, timeout=120).returncode != 0:
                raise CheckError("restore_no_ram")
            path = "/Volumes/" + name
            os.chmod(path, 0o700)
            try:
                yield path
            finally:
                shutil.rmtree(path, ignore_errors=True)
        finally:
            run(["hdiutil", "detach", device, "-force"], capture_output=True, timeout=60)
        return
    raise CheckError("restore_no_ram")


def fetch_key(source, run=subprocess.run):
    """-> the ASCII-armoured private key as bytes, straight from Bitwarden. `source` is ("bws", secret id) or ("bw", item name). The value never touches disk, argv or the environment."""
    kind, ref = source
    if kind == "prompt":                                                                 # Jim types it: hidden, read from the terminal itself, never an argument, an environment value or history
        try:
            open("/dev/tty").close()
        except OSError:
            raise CheckError("restore_no_tty") from None
        return getpass.getpass("Backup key passphrase (typing is hidden): ").encode()
    if kind == "bws":
        done = run(["bws", "secret", "get", ref, "--output", "json"], capture_output=True, timeout=60)
        if done.returncode != 0:
            raise CheckError("restore_key_fetch")
        try:
            return json.loads(done.stdout)["value"].encode()
        except (ValueError, KeyError):
            raise CheckError("restore_key_fetch") from None
    if kind == "bw":
        done = run(["bw", "get", "notes", ref], capture_output=True, timeout=60)
        if done.returncode != 0 or not done.stdout.strip():
            raise CheckError("restore_key_fetch")
        return done.stdout
    raise CheckError("restore_key_source")


def gpg_base(folder, passfile=None):
    home = os.path.join(folder, "gnupg")
    os.makedirs(home, mode=0o700, exist_ok=True)
    phrase = ["--passphrase-file", passfile] if passfile else ["--passphrase", ""]
    return ["gpg", "--batch", "--no-tty", "--quiet", "--pinentry-mode", "loopback"] + phrase + ["--homedir", home]


def import_key(folder, key, run=subprocess.run):
    done = run(gpg_base(folder) + ["--import"], input=key, capture_output=True, timeout=120)
    if done.returncode != 0:
        raise CheckError("restore_key_import")


def decrypt(folder, source, dest, run=subprocess.run, passfile=None):
    done = run(gpg_base(folder, passfile) + ["--output", dest, "--decrypt", source], capture_output=True, timeout=600)
    if done.returncode != 0 or not os.path.isfile(dest):
        raise CheckError("restore_decrypt")


def manifest_hash(archive, prefix):
    """-> (file count, one sha256 over the sorted lines 'name size sha256') for the members under `prefix`: a fingerprint that can be recomputed on the live folder, no names printed."""
    lines = []
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if member.isreg() and member.name.startswith(prefix):
                digest = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
                lines.append("%s %d %s" % (member.name[len(prefix):], member.size, digest))
    return len(lines), hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


def check_archive(plain, work):
    """verify_restore on every member; -> counts only."""
    report = vr.verify(plain, prefix="", min_files=1, work=work)
    return {"files": report["files"], "databases": len(report["databases"])}


def run_check(archive, source, run=subprocess.run, ram=ram_folder, platform=sys.platform, out=sys.stdout, manifests=(), passphrase=None):
    try:
        if not os.path.isfile(archive):
            raise CheckError("restore_no_archive")
        with (ram(run=run, platform=platform) if ram is ram_folder else ram()) as folder:
            key = fetch_key(source, run)
            import_key(folder, key, run)
            del key
            passfile = None
            if passphrase:                                                               # the key's passphrase, if it has one, also comes from Bitwarden and lives only in the RAM folder
                passfile = os.path.join(folder, "pp")
                fd = os.open(passfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(fetch_key(passphrase, run))
            outer = os.path.join(folder, "outer.tar.gz")
            if archive.endswith((".gpg", ".asc", ".pgp")):
                decrypt(folder, archive, outer, run, passfile)
            else:
                raise CheckError("RESTORE_UNREADABLE")
            result = {"archives": 1}
            with tarfile.open(outer, "r:gz") as tar:
                inner = [m for m in tar.getmembers() if m.isreg() and MAC_INNER.match(m.name)]
                if inner:
                    sealed = os.path.join(folder, "mac.tar.gz.gpg")
                    with tar.extractfile(inner[-1]) as src, open(sealed, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            plains = [("nightly", outer)]
            if inner:
                mac_plain = os.path.join(folder, "mac.tar.gz")
                decrypt(folder, sealed, mac_plain, run, passfile)
                plains.append(("mac", mac_plain))
                result["archives"] = 2
            for label, plain in plains:
                counts = check_archive(plain, os.path.join(folder, "x-" + label))
                result["%s_files" % label], result["%s_databases" % label] = counts["files"], counts["databases"]
                for prefix in manifests:
                    n, h = manifest_hash(plain, prefix)
                    if n:
                        result["%s_manifest_%s" % (label, prefix.strip("/").replace("/", "_"))] = "%d files, %s" % (n, h[:16])
        out.write("PASS " + " ".join("%s=%s" % kv for kv in sorted(result.items())) + "\n")
        return 0
    except (CheckError, vr.RestoreError) as error:
        out.write("FAIL %s\n" % error)
        return 1
    except Exception:                                                                    # any other failure is a fixed code, never a message that could carry a name or a value
        out.write("FAIL RESTORE_UNEXPECTED\n")
        return 1


def main(argv=None, out=sys.stdout):
    argv = list(sys.argv[1:] if argv is None else argv)
    archive, source, manifests, passphrase = None, None, [], None
    while argv:
        arg = argv.pop(0)
        if arg == "--bws-secret" and argv:
            source = ("bws", argv.pop(0))
        elif arg == "--bw-item" and argv:
            source = ("bw", argv.pop(0))
        elif arg == "--passphrase-bws" and argv:
            passphrase = ("bws", argv.pop(0))
        elif arg == "--passphrase-prompt":
            passphrase = ("prompt", "")
        elif arg == "--passphrase-bw" and argv:
            passphrase = ("bw", argv.pop(0))
        elif arg == "--manifest" and argv:
            manifests.append(argv.pop(0))
        elif archive is None and not arg.startswith("--"):
            archive = arg
        else:
            archive = None
            break
    if archive and not source and os.path.isfile(ID_FILE):
        source = ("bws", open(ID_FILE).read().strip())                                   # no id on the command line: read it from the local file
    if not archive or not source:
        out.write("usage: restore_check ARCHIVE.gpg (--bws-secret SECRET_ID | --bw-item NAME) [--passphrase-prompt | --passphrase-bws ID | --passphrase-bw NAME] [--manifest code/lifeos-fit/ ...]\n")
        return 2
    return run_check(archive, source, out=out, manifests=tuple(manifests), passphrase=passphrase)


if __name__ == "__main__":
    sys.exit(main())
