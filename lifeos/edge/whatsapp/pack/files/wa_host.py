"""LIFE-643 helper for the WhatsApp pack steps (python3 standard library only, so the same file runs on Oracle and in the tests). Prints names, modes and counts only; it never opens a session file (only the unit file is hashed)."""
import hashlib
import json
import os
import socket
import stat
import sys


def walk(trees):
    for tree in trees:
        yield tree, os.lstat(tree)
        for base, dirs, files in os.walk(tree):
            for name in sorted(dirs + files):
                path = os.path.join(base, name)
                yield path, os.lstat(path)


def cmd_check(owner, trees):
    """Refuse (exit 1, one fixed word) on a link, an entry not owned by the owner, or anything but files, folders and the one answer socket."""
    for path, info in walk(trees):
        if stat.S_ISLNK(info.st_mode):
            print("symlink")
            return 1
        if info.st_uid != int(owner):
            print("owner")
            return 1
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or (stat.S_ISSOCK(info.st_mode) and os.path.basename(path) == "attention.sock")):
            print("odd_type")
            return 1
    return 0


def cmd_holders(procdir, owner, trees):
    """Count processes owned by neither root nor the owner that hold a file or working folder under the trees."""
    prefixes = tuple(t.rstrip("/") + "/" for t in trees) + tuple(trees)
    count = 0
    for entry in os.listdir(procdir):
        if not entry.isdigit():
            continue
        base = os.path.join(procdir, entry)
        try:
            if os.stat(base).st_uid in (0, int(owner)):
                continue
            links = [os.path.join(base, "cwd")] + [os.path.join(base, "fd", f) for f in os.listdir(os.path.join(base, "fd"))]
        except OSError:
            continue
        for link in links:
            try:
                if os.readlink(link).startswith(prefixes):
                    count += 1
                    break
            except OSError:
                pass
    print(count)
    return 0


def cmd_counts(trees):
    seen = {}
    for _path, info in walk(trees):
        kind = "d" if stat.S_ISDIR(info.st_mode) else "f" if stat.S_ISREG(info.st_mode) else None
        if kind:
            key = "%s%o" % (kind, stat.S_IMODE(info.st_mode))
            seen[key] = seen.get(key, 0) + 1
    print(" ".join("%s=%d" % item for item in sorted(seen.items())))
    return 0


def cmd_record(listing, trees):
    """The names-and-modes list that lets the step put every mode back (root-only file, no contents)."""
    rows = [json.dumps([stat.S_IMODE(info.st_mode), path]) for path, info in walk(trees) if stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)]
    tmp = listing + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(rows) + "\n")
    os.replace(tmp, listing)
    print(len(rows))
    return 0


def cmd_apply(trees):
    changed = 0
    for path, info in list(walk(trees)):
        want = 0o700 if stat.S_ISDIR(info.st_mode) else 0o600 if stat.S_ISREG(info.st_mode) else None
        if want is not None and stat.S_IMODE(info.st_mode) != want:
            os.chmod(path, want)
            changed += 1
    print(changed)
    return 0


def cmd_rollback(listing):
    failed = restored = 0
    with open(listing) as handle:
        for line in handle:
            if not line.strip():
                continue
            mode, path = json.loads(line)
            try:
                if not os.path.islink(path):
                    os.chmod(path, mode)
                    restored += 1
            except OSError:
                failed += 1
    print("restored=%d failed=%d" % (restored, failed))
    return 1 if failed else 0


def cmd_connect(path):
    """Connect to the answer socket and count the bytes it sends back; the answer is dropped, never printed."""
    total = 0
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(5)
        sock.connect(path)
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            total += len(chunk)
    print(total)
    return 0 if total else 1


def cmd_sha256(path):
    with open(path, "rb") as handle:
        print(hashlib.sha256(handle.read()).hexdigest())
    return 0


def main(argv):
    cmd, args = argv[0], argv[1:]
    table = {
        "check": lambda: cmd_check(args[0], args[1:]),
        "holders": lambda: cmd_holders(args[0], args[1], args[2:]),
        "counts": lambda: cmd_counts(args),
        "record": lambda: cmd_record(args[0], args[1:]),
        "apply": lambda: cmd_apply(args),
        "rollback": lambda: cmd_rollback(args[0]),
        "connect": lambda: cmd_connect(args[0]),
        "sha256": lambda: cmd_sha256(args[0]),
        "uid": lambda: print(os.stat(args[0]).st_uid) or 0,
        "mode": lambda: print("%o" % stat.S_IMODE(os.stat(args[0]).st_mode)) or 0,
    }
    try:
        return table[cmd]()
    except (KeyError, IndexError, OSError, ValueError):
        print("error")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
