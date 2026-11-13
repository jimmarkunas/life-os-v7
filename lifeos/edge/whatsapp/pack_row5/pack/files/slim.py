"""LIFE-643 row 5 helper (python3 standard library only): slim an existing npm tree by REMOVING packages, and check it against the committed lockfile. No npm, no network, no script runs. Prints fixed words and counts only.

    slim.py prune DIR LOCKFILE      DIR holds a copy of node_modules/ ; delete every package the lockfile flags optional or peer-only (the sharp / @img image libraries), drop them from the hidden lockfile and remove dangling .bin links
    slim.py verify DIR LOCKFILE     exit 0 only if the package list (hidden lockfile and folders) equals the lockfile's non-optional, non-peer packages and no image library is left"""
import json
import os
import shutil
import sys


def expected(lock):
    return sorted(name for name, meta in lock["packages"].items() if name and not meta.get("optional") and not meta.get("peer"))


def omitted(lock):
    return sorted((name for name, meta in lock["packages"].items() if name and (meta.get("optional") or meta.get("peer"))), key=lambda n: -n.count("/"))


def load(lockfile):
    with open(lockfile) as handle:
        return json.load(handle)


def prune(build, lockfile):
    lock = load(lockfile)
    modules = os.path.join(build, "node_modules")
    gone = 0
    for name in omitted(lock):                                    # deepest paths first; name is "node_modules/<package>" as in the lockfile
        path = os.path.join(build, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
            gone += 1
    scope_dirs = [os.path.join(modules, d) for d in os.listdir(modules) if d.startswith("@")]
    for scope in scope_dirs:                                      # an emptied scope folder (@img) goes too
        if os.path.isdir(scope) and not os.listdir(scope):
            os.rmdir(scope)
    hidden = os.path.join(modules, ".package-lock.json")
    try:
        with open(hidden) as handle:
            data = json.load(handle)
        drop = set(omitted(lock))
        data["packages"] = {k: v for k, v in data["packages"].items() if k not in drop}
        with open(hidden, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
    except (OSError, ValueError, KeyError):
        print("no_hidden_lockfile")
        return 1
    binfolder = os.path.join(modules, ".bin")
    dangling = 0
    if os.path.isdir(binfolder):
        for entry in os.listdir(binfolder):
            link = os.path.join(binfolder, entry)
            if os.path.islink(link) and not os.path.exists(link):
                os.unlink(link)
                dangling += 1
    print("pruned=%d dangling_links_removed=%d" % (gone, dangling))
    return 0


def verify(build, lockfile):
    lock = load(lockfile)
    want = expected(lock)
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
    if any(os.path.exists(os.path.join(build, name)) for name in omitted(lock)):
        print("omitted_package_present")
        return 1
    if have != want:
        print("count_differs have=%d want=%d" % (len(have), len(want)))
        return 1
    missing = [name for name in want if not os.path.isfile(os.path.join(build, name, "package.json"))]
    if missing:
        print("package_folder_missing count=%d" % len(missing))
        return 1
    print("packages=%d want=%d image_libraries=0" % (len(have), len(want)))
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) == 4 else ""
    sys.exit({"prune": prune, "verify": verify}[command](sys.argv[2], sys.argv[3]) if command in ("prune", "verify") else 2)
