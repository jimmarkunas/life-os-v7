"""LIFE-643 row 1 helper for the move of WhatsApp Attention to its own user (python3 standard library only, so the same file runs on Oracle and in the tests).
Prints names, modes, ids and counts only. It copies session files without ever printing or inspecting what is in them; nothing here reads a session file's contents except to copy the bytes."""
import hashlib
import os
import shutil
import socket
import stat
import sys

NO_CHOWN = False                                           # `--no-chown` as the first argument: tests only (no root, no second user); the host run never passes it


def walk(tree):
    yield "", os.lstat(tree)
    for base, dirs, files in os.walk(tree):
        for name in sorted(dirs + files):
            path = os.path.join(base, name)
            yield os.path.relpath(path, tree), os.lstat(path)


def own(path, uid, gid):
    if not NO_CHOWN:
        os.lchown(path, int(uid), int(gid))


def mkparents(path, uid, gid):
    """Create the missing folders above `path` top-down: 700, owned by the new user (so no folder in its home is left root-owned)."""
    missing = []
    parent = os.path.dirname(path)
    while parent and not os.path.isdir(parent):
        missing.append(parent)
        parent = os.path.dirname(parent)
    for folder in reversed(missing):
        os.mkdir(folder, 0o700)
        os.chmod(folder, 0o700)
        own(folder, uid, gid)


def cmd_check(owner, tree):
    """Refuse (exit 1, one fixed word) on a link, an entry not owned by the owner, or anything but files, folders and the one answer socket."""
    for rel, info in walk(tree):
        if stat.S_ISLNK(info.st_mode):
            print("symlink")
            return 1
        if info.st_uid != int(owner):
            print("owner")
            return 1
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or (stat.S_ISSOCK(info.st_mode) and os.path.basename(rel) == "attention.sock")):
            print("odd_type")
            return 1
    return 0


def tally(tree):
    files = dirs = size = 0
    for _rel, info in walk(tree):
        if stat.S_ISDIR(info.st_mode):
            dirs += 1
        elif stat.S_ISREG(info.st_mode):
            files += 1
            size += info.st_size
    return files, dirs, size


def cmd_counts(tree):
    files, dirs, size = tally(tree)
    modes = {}
    for _rel, info in walk(tree):
        kind = "d" if stat.S_ISDIR(info.st_mode) else "f" if stat.S_ISREG(info.st_mode) else None
        if kind:
            key = "%s%o" % (kind, stat.S_IMODE(info.st_mode))
            modes[key] = modes.get(key, 0) + 1
    print("files=%d dirs=%d bytes=%d modes: %s" % (files, dirs, size, " ".join("%s=%d" % item for item in sorted(modes.items()))))
    return 0


def cmd_copy_session(src, dst, uid, gid):
    """Copy files and folders (never sockets) to dst: folders 700, files 600, owner uid:gid. dst must not exist."""
    if os.path.lexists(dst):
        print("dst_exists")
        return 1
    mkparents(dst, uid, gid)
    for rel, info in walk(src):
        target = os.path.join(dst, rel) if rel else dst
        if stat.S_ISDIR(info.st_mode):
            os.makedirs(target, mode=0o700)
            os.chmod(target, 0o700)
            own(target, uid, gid)
        elif stat.S_ISREG(info.st_mode):
            shutil.copyfile(os.path.join(src, rel), target)
            os.chmod(target, 0o600)
            own(target, uid, gid)
        elif stat.S_ISSOCK(info.st_mode):
            continue
        else:
            print("odd_type")
            return 1
    print("copied files=%d dirs=%d bytes=%d" % tally(dst))
    return 0


def listing(tree):
    out = {}
    for rel, info in walk(tree):
        if stat.S_ISDIR(info.st_mode):
            out[rel] = "d"
        elif stat.S_ISREG(info.st_mode):
            out[rel] = info.st_size
    return out


def cmd_same(src, dst):
    """Same names, kinds and sizes (the socket does not count)."""
    a, b = listing(src), listing(dst)
    if a == b:
        print("same entries=%d" % len(a))
        return 0
    print("differ")
    return 1


def cmd_copy_file(src, dst, uid, gid):
    mkparents(dst, uid, gid)
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o600)
    own(dst, uid, gid)
    print("copied")
    return 0


def cmd_install_tree(src, dst):
    """Read-only program tree for the new user: root-owned, nobody can write, everything readable, executables stay executable; links are kept as links."""
    count = size = 0
    for rel, info in walk(src):
        source, target = os.path.join(src, rel), os.path.join(dst, rel) if rel else dst
        if stat.S_ISLNK(info.st_mode):
            os.symlink(os.readlink(source), target)
            if not NO_CHOWN:
                os.lchown(target, 0, 0)
        elif stat.S_ISDIR(info.st_mode):
            os.makedirs(target, mode=0o755, exist_ok=True)
            os.chmod(target, 0o755)
            if not NO_CHOWN:
                os.chown(target, 0, 0)
        elif stat.S_ISREG(info.st_mode):
            shutil.copyfile(source, target)
            os.chmod(target, 0o755 if info.st_mode & 0o111 else 0o644)
            if not NO_CHOWN:
                os.chown(target, 0, 0)
            size += info.st_size
        else:
            print("odd_type")
            return 1
        count += 1
    print("installed entries=%d bytes=%d" % (count, size))
    return 0


def cmd_tree_summary(tree):
    """entries and bytes, the same measure install-tree prints (links count as entries)."""
    count = size = 0
    for _rel, info in walk(tree):
        count += 1
        size += info.st_size if stat.S_ISREG(info.st_mode) else 0
    print("entries=%d bytes=%d" % (count, size))
    return 0


def cmd_wipe(path, keep_under):
    """Remove a folder, but only one that lies strictly under keep_under (the new user's own home)."""
    real, base = os.path.realpath(path), os.path.realpath(keep_under)
    if not real.startswith(base.rstrip("/") + "/"):
        print("refused")
        return 1
    if os.path.isdir(real):
        shutil.rmtree(real)
    print("wiped")
    return 0


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


def cmd_canlist(path):
    """Can this user list the folder? (run as ubuntu against the new state folder: it must say denied)"""
    try:
        os.listdir(path)
    except OSError:
        print("denied")
        return 1
    print("readable")
    return 0


def cmd_sockinfo(path):
    info = os.stat(path)
    print("%d %d %o %s" % (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), "socket" if stat.S_ISSOCK(info.st_mode) else "notsocket"))
    return 0


def cmd_procuid(procdir, pid):
    with open(os.path.join(procdir, pid, "status")) as handle:        # kernel status of a process: ids and names only
        for line in handle:
            if line.startswith("Uid:"):
                print(line.split()[1])
                return 0
    return 1


def cmd_sha256(path):
    with open(path, "rb") as handle:
        print(hashlib.sha256(handle.read()).hexdigest())
    return 0


def main(argv):
    global NO_CHOWN
    if argv and argv[0] == "--no-chown":
        NO_CHOWN, argv = True, argv[1:]
    cmd, args = argv[0], argv[1:]
    table = {
        "check": lambda: cmd_check(args[0], args[1]),
        "counts": lambda: cmd_counts(args[0]),
        "copy-session": lambda: cmd_copy_session(*args[:4]),
        "same": lambda: cmd_same(args[0], args[1]),
        "copy-file": lambda: cmd_copy_file(*args[:4]),
        "install-tree": lambda: cmd_install_tree(args[0], args[1]),
        "tree-summary": lambda: cmd_tree_summary(args[0]),
        "wipe": lambda: cmd_wipe(args[0], args[1]),
        "connect": lambda: cmd_connect(args[0]),
        "canlist": lambda: cmd_canlist(args[0]),
        "sockinfo": lambda: cmd_sockinfo(args[0]),
        "procuid": lambda: cmd_procuid(args[0], args[1]),
        "sha256": lambda: cmd_sha256(args[0]),
        "mode": lambda: print("%o" % stat.S_IMODE(os.stat(args[0]).st_mode)) or 0,
    }
    try:
        return table[cmd]()
    except (KeyError, IndexError, OSError, ValueError):
        print("error")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
