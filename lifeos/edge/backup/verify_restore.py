"""LIFE-318: check a DECRYPTED nightly backup (the .tar.gz Jim gets by decrypting the Drive file with his private key, see README.md) on Jim's Mac, without touching any live file.
  python3 verify_restore.py ARCHIVE.tar.gz [--prefix hermes/] [--min-files 1]
It unpacks into a throw-away folder (member names are checked first: nothing absolute, no `..`, regular files only, size capped), then for every `*.sqlite` or `*.db` file
under the prefix runs `PRAGMA integrity_check` and counts the rows of every table. It prints file names' COUNT, database names with their integrity result and row counts per
table; never a file's content. Exit 0 only when the prefix holds at least --min-files files, every database passes, and no member was unsafe. The folder is always removed.
Every database is checked (one bad one does not hide the others); a failure names the database and SQLite's own error text, never its content.
Stdlib only. Fixed codes on failure: RESTORE_UNSAFE_PATH, RESTORE_TOO_BIG, RESTORE_NOTHING_UNDER_PREFIX, RESTORE_DB_CORRUPT (SQLite reports damage), RESTORE_DB_UNCHECKED
(SQLite cannot run the check here, for example a missing extension module: not proof of damage), RESTORE_UNREADABLE."""
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import urllib.parse

MAX_TOTAL = 400 * 1024 * 1024


class RestoreError(RuntimeError):
    """The message is always a fixed code; `detail` names the databases and SQLite's error text (never content)."""

    def __init__(self, code, detail=""):
        super().__init__(code)
        self.detail = detail


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


MODULE_HINTS = ("no such module", "no such tokenizer", "unknown tokenizer", "no such function", "not authorized", "unsupported")


def _open(path):
    """Open the extracted COPY (a private throw-away file, so nothing live is touched). A plain open is used first: Hermes's databases are WAL-mode, and a
    read-only URI open of a WAL database fails with 'unable to open database file' when its helper files cannot be made (LIFE-318, Nov 6 2026: all nine databases
    were wrongly reported corrupt). If that still fails, an immutable open (no helper files, no locking) is tried with the path properly quoted. The SQLite
    version is added to a failure so a machine-specific problem is visible."""
    errors = []
    for how in ("plain", "immutable"):
        try:
            if how == "plain":
                db = sqlite3.connect(path)
            else:
                db = sqlite3.connect("file:%s?mode=ro&immutable=1" % urllib.parse.quote(path), uri=True)
            db.execute("select count(*) from sqlite_master").fetchone()                  # opening is lazy: this is what really touches the file
            return db
        except sqlite3.DatabaseError as error:
            errors.append("%s open: %s" % (how, error))
    raise RestoreError("RESTORE_DB_CORRUPT", "%s [sqlite %s]" % ("; ".join(errors), sqlite3.sqlite_version))


def tables(path):
    """{table: rows}. Raises RestoreError RESTORE_DB_CORRUPT (damage reported by SQLite) or RESTORE_DB_UNCHECKED (the check could not run here)."""
    db = _open(path)
    try:
        try:
            result = db.execute("pragma integrity_check").fetchall()
        except sqlite3.DatabaseError as error:
            text = str(error)
            code = "RESTORE_DB_UNCHECKED" if any(h in text.lower() for h in MODULE_HINTS) else "RESTORE_DB_CORRUPT"
            raise RestoreError(code, text) from None
        if result != [("ok",)]:
            raise RestoreError("RESTORE_DB_CORRUPT", "; ".join(str(r[0]) for r in result[:3]))
        out = {}
        for (name,) in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name").fetchall():
            try:
                out[name] = db.execute('select count(*) from "%s"' % name.replace('"', '""')).fetchone()[0]
            except sqlite3.DatabaseError:
                out[name] = None                                  # a virtual table whose module is not loaded here: not counted
        return out
    except sqlite3.DatabaseError as error:
        raise RestoreError("RESTORE_DB_CORRUPT", str(error)) from None
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
            failures = []
            for member in wanted:
                dest = os.path.join(work, member.name)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with tar.extractfile(member) as source, open(dest, "wb") as handle:
                    shutil.copyfileobj(source, handle)
                if member.name.endswith((".db", ".sqlite")):
                    try:
                        databases[member.name] = tables(dest)
                    except RestoreError as error:
                        failures.append((member.name, str(error), error.detail))
            if failures:
                first = failures[0][1]
                raise RestoreError(first if len({f[1] for f in failures}) == 1 else "RESTORE_DB_CORRUPT",
                                   " | ".join("%s: %s (%s)" % (n, c, d or "no detail") for n, c, d in failures))
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
        if getattr(error, "detail", ""):
            for item in error.detail.split(" | "):
                out.write("  %s\n" % item)
        return 1
    out.write("files under %s: %d; databases: %d\n" % (prefix, report["files"], len(report["databases"])))
    for name, counts in sorted(report["databases"].items()):
        out.write("  %s: integrity ok; rows %s\n" % (name, ", ".join("%s=%s" % (t, "n/a" if c is None else c) for t, c in counts.items()) or "no tables"))
    out.write("OK: restore check passed; the check folder was removed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
