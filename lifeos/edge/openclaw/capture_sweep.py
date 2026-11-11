"""LIFE-575: remove OpenClaw plugin-capture folders whose gateway process is gone. Stdlib only, runs as the ubuntu user on the Oracle VM.

    python3 capture_sweep.py [--delete] [--root DIR] [--proc DIR] [--min-age SECONDS]

Each gateway start makes one UUID-named folder under ~/.openclaw/tmp/plugin-captures and the next start leaves the old one behind (LIFE-574: 200 to 430 MB each).
A folder is LIVE when any process of this user has it open, mapped, as its working folder or in its environment; otherwise it is an ORPHAN. Default is a dry run.
Never deleted: a live folder, a folder changed in the last --min-age seconds (default 600), anything whose name is not a UUID, a symlink, a file.
If the process table cannot be read, nothing is deleted and the exit code is 2 (unknown is never an orphan). Output: counts and the first 8 characters of folder names."""
import argparse
import os
import re
import shutil
import sys
import time

ROOT = os.path.expanduser("~/.openclaw/tmp/plugin-captures")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
MIN_AGE_SECONDS = 600


class Unreadable(Exception):
    pass


def _texts(pid_dir):
    """Everything one process says about the files it uses; FileNotFoundError (it just exited) reads as nothing, any other failure raises."""
    out, failed = [], 0
    for name in ("environ", "maps"):
        try:
            with open(os.path.join(pid_dir, name), "rb") as f:
                out.append(f.read().replace(b"\0", b"\n").decode("utf-8", "replace"))
        except FileNotFoundError:
            return out
        except OSError:
            failed += 1
    try:
        out.append(os.readlink(os.path.join(pid_dir, "cwd")))
        fd_dir = os.path.join(pid_dir, "fd")
        for fd in os.listdir(fd_dir):
            try:
                out.append(os.readlink(os.path.join(fd_dir, fd)))
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        return out
    except OSError:
        failed += 1
    if failed:
        raise Unreadable(os.path.basename(pid_dir))
    return out


def process_text(proc_root):
    """-> one string with what every process of this user references. Raises Unreadable when the table or one of our own processes cannot be read."""
    try:
        pids = [n for n in os.listdir(proc_root) if n.isdigit()]
    except OSError as error:
        raise Unreadable(str(error)) from error
    mine, parts = 0, []
    for pid in pids:
        pid_dir = os.path.join(proc_root, pid)
        try:
            if os.stat(pid_dir).st_uid != os.getuid():
                continue
        except OSError:
            continue
        mine += 1
        parts.extend(_texts(pid_dir))
    if mine == 0:
        raise Unreadable("no process of this user")
    return "\n".join(parts)


def classify(root, text, now, min_age=MIN_AGE_SECONDS):
    """-> {'live': [...], 'orphan': [...], 'recent': [...], 'skipped': n}; names are folder names."""
    result = {"live": [], "orphan": [], "recent": [], "skipped": 0}
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not UUID.match(name) or os.path.islink(path) or not os.path.isdir(path):
            result["skipped"] += 1
        elif name in text:
            result["live"].append(name)
        elif now - os.stat(path).st_mtime < min_age:
            result["recent"].append(name)
        else:
            result["orphan"].append(name)
    return result


def main(argv=None, now=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true", help="remove the orphans (default: only report)")
    parser.add_argument("--root", default=ROOT)
    parser.add_argument("--proc", default="/proc")
    parser.add_argument("--min-age", type=int, default=MIN_AGE_SECONDS)
    args = parser.parse_args(argv)
    if not os.path.isdir(args.root):
        print("capture-sweep: no capture folder, nothing to do")
        return 0
    try:
        text = process_text(args.proc)
    except Unreadable:
        print("capture-sweep: CAPTURE_SWEEP_PROCESS_TABLE_UNREADABLE - nothing deleted", file=sys.stderr)
        return 2
    found = classify(args.root, text, time.time() if now is None else now, args.min_age)
    removed = 0
    if args.delete:
        for name in found["orphan"]:
            shutil.rmtree(os.path.join(args.root, name))
            removed += 1
    print(f"capture-sweep: live {len(found['live'])} orphan {len(found['orphan'])} recent {len(found['recent'])} skipped {found['skipped']} "
          f"removed {removed} mode {'delete' if args.delete else 'dry-run'} orphans {[n[:8] for n in found['orphan']]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
