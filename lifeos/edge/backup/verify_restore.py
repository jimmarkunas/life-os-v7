"""LIFE-318: check a DECRYPTED nightly backup (the .tar.gz Jim gets by decrypting the Drive file with his private key, see README.md) on Jim's Mac, without touching any live file.
  python3 verify_restore.py ARCHIVE.tar.gz [--prefix hermes/] [--min-files 1]
It unpacks into a throw-away folder (member names are checked first: nothing absolute, no `..`, regular files only, size capped), then for every `*.sqlite` or `*.db` file
under the prefix runs `PRAGMA integrity_check` and counts the rows of every table. It prints file names' COUNT, database names with their integrity result and row counts per
table; never a file's content. Exit 0 only when the prefix holds at least --min-files files, every database passes, and no member was unsafe. The folder is always removed.
Stdlib only. Fixed codes on failure: RESTORE_UNSAFE_PATH, RESTORE_TOO_BIG, RESTORE_NOTHING_UNDER_PREFIX, RESTORE_DB_CORRUPT, RESTORE_UNREADABLE."""
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile

MAX_TOTAL = 400 * 1024 * 1024


class RestoreError(RuntimeError):
    """The message is always a fixed code."""


def safe_members(tar):
    total = 0
    for member in tar.getmembers():
        name = member.name
        if member.isdir():
            continue
        if not member.isreg() or name.startswith("/") or ".." in name.split("/") or "\\" in name or not name:
            raise RestoreError("RESTORE_UNSAFE_PATH")
        total += member.size
        if total > MAX_TOTAL:
            raise RestoreError("RESTORE_TOO_BIG")
        yield member


def tables(path):
    db = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        if db.execute("pragma integrity_check").fetchone() != ("ok",):
            raise RestoreError("RESTORE_DB_CORRUPT")
        out = {}
        for (name,) in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name").fetchall():
            try:
                out[name] = db.execute('select count(*) from "%s"' % name.replace('"', '""')).fetchone()[0]
            except sqlite3.DatabaseError:
                out[name] = None                                  # a virtual table whose module is not loaded here: not counted
        return out
    except sqlite3.DatabaseError:
        raise RestoreError("RESTORE_DB_CORRUPT") from None
    finally:
        db.close()


def verify(archive, prefix="hermes/", min_files=1, work=None):
    """-> {"files": n, "databases": {name: {table: rows}}}. Raises RestoreError(code)."""
    own = work is None
    work = work or tempfile.mkdtemp(prefix="restore-check-")
    try:
        try:
            tar = tarfile.open(archive, "r:gz")
        except (OSError, tarfile.TarError):
            raise RestoreError("RESTORE_UNREADABLE") from None
        with tar:
            wanted = [m for m in safe_members(tar) if m.name.startswith(prefix)]
            if len(wanted) < min_files:
                raise RestoreError("RESTORE_NOTHING_UNDER_PREFIX")
            databases = {}
            for member in wanted:
                dest = os.path.join(work, member.name)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with tar.extractfile(member) as source, open(dest, "wb") as handle:
                    shutil.copyfileobj(source, handle)
                if member.name.endswith((".db", ".sqlite")):
                    databases[member.name] = tables(dest)
        return {"files": len(wanted), "databases": databases}
    finally:
        if own:
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None, out=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    prefix, min_files, archive = "hermes/", 1, None
    while argv:
        arg = argv.pop(0)
        if arg == "--prefix" and argv:
            prefix = argv.pop(0)
        elif arg == "--min-files" and argv:
            min_files = int(argv.pop(0))
        elif archive is None:
            archive = arg
        else:
            out.write("usage: verify_restore.py ARCHIVE.tar.gz [--prefix hermes/] [--min-files N]\n")
            return 2
    if archive is None:
        out.write("usage: verify_restore.py ARCHIVE.tar.gz [--prefix hermes/] [--min-files N]\n")
        return 2
    try:
        report = verify(archive, prefix, min_files)
    except RestoreError as error:
        out.write("RESTORE FAILED: %s\n" % error)
        return 1
    out.write("files under %s: %d; databases: %d\n" % (prefix, report["files"], len(report["databases"])))
    for name, counts in sorted(report["databases"].items()):
        out.write("  %s: integrity ok; rows %s\n" % (name, ", ".join("%s=%s" % (t, "n/a" if c is None else c) for t, c in counts.items()) or "no tables"))
    out.write("OK: restore check passed; the check folder was removed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
