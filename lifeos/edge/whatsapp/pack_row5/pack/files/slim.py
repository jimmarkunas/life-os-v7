"""LIFE-643 row 5 helper (python3 standard library only): check a freshly built npm tree against the committed lockfile. Prints fixed words and counts only.

    slim.py verify DIR LOCKFILE     DIR holds node_modules/ ; exit 0 only if its package list equals the lockfile's non-optional, non-peer packages and no sharp / @img package is there"""
import json
import os
import sys


def expected(lock):
    return sorted(name for name, meta in lock["packages"].items() if name and not meta.get("optional") and not meta.get("peer"))


def verify(build, lockfile):
    with open(lockfile) as handle:
        want = expected(json.load(handle))
    modules = os.path.join(build, "node_modules")
    try:
        with open(os.path.join(modules, ".package-lock.json")) as handle:
            have = sorted(name for name in json.load(handle)["packages"] if name)
    except (OSError, ValueError, KeyError):
        print("no_hidden_lockfile")
        return 1
    if os.path.exists(os.path.join(modules, "sharp")) or os.path.exists(os.path.join(modules, "@img")) or any("sharp" in n or "@img" in n for n in have):
        print("image_library_present")
        return 1
    if have != want:
        print("count_differs have=%d want=%d" % (len(have), len(want)))
        return 1
    print("packages=%d want=%d image_libraries=0" % (len(have), len(want)))
    return 0


if __name__ == "__main__":
    sys.exit(verify(sys.argv[2], sys.argv[3]) if len(sys.argv) == 4 and sys.argv[1] == "verify" else 2)
