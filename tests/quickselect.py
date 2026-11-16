"""The quick local gate (LIFE-763 CI speed): `python -m tests.quickselect run --base BASE` runs, in parallel processes, the tests a push most often needs: every contract test, the repo-wide
scanners (testindex.REPO_WIDE), the tests that name or mirror each changed file, and any changed test. It never escalates to the whole suite: CI runs that (Jim, Nov 16 2026). `select` only prints the targets."""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

from tests import shard, testindex

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_JOBS = 8


def changed_files(base, root=ROOT):
    out = subprocess.run(["git", "diff", "--name-only", "--diff-filter=ACMRD", base, "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def importers(dotted_name, stem, root=ROOT):
    """Test modules whose text names the production module (`lifeos.pkg.stem`, or `from lifeos.pkg import stem`)."""
    package = dotted_name.rsplit(".", 1)[0]
    pattern = re.compile(r"\b%s\b|from\s+%s\s+import[^\n]*\b%s\b" % (re.escape(dotted_name), re.escape(package), re.escape(stem)))
    found = set()
    for path in pathlib.Path(root, "tests").rglob("test_*.py"):
        try:
            if pattern.search(path.read_text(errors="replace")):
                found.add(testindex.dotted(str(path.relative_to(root))))
        except OSError:
            continue
    return found


def quick_targets(changed, root=ROOT):
    """-> sorted dotted test modules: contracts, the repo-wide scanners, and what each changed file maps to. Pure apart from reading the tree."""
    known = testindex.module_set(root)
    targets = set(testindex.contract_modules(root)) | {m for m in testindex.REPO_WIDE if m in known}
    for path in changed:
        p = pathlib.Path(path)
        if path.startswith("tests/") and p.name.startswith("test_") and (pathlib.Path(root) / path).is_file():
            targets.add(testindex.dotted(path))
        elif path.startswith("lifeos/") and path.endswith(".py") and p.name != "__init__.py":
            rel = path[len("lifeos/"):]
            package = rel.split("/")[0] if "/" in rel else None
            stem = p.stem
            dotted_name = path[:-3].replace("/", ".")
            targets |= importers(dotted_name, stem, root)
            if package and package != "platform":                           # a feature package: its whole mirror test folder
                targets |= set(testindex.test_modules(root, "tests/" + package))
            elif package == "platform":                                     # the shared layer: the tests named after the module, plus every importer found above
                targets |= {m for m in known if m.startswith("tests.platform.test_" + stem)}
            else:
                targets |= {m for m in known if m == "tests.test_" + stem}
    return sorted(t for t in targets if t in known)


def partition(modules, n):
    where = shard.assignment(set(modules), n)
    groups = [[] for _ in range(n)]
    for m in sorted(modules):
        groups[where[m]].append(m)
    return [g for g in groups if g]


def run(modules, jobs):
    python = sys.executable
    groups = partition(modules, max(1, min(jobs, len(modules))))
    logs, procs = [], []
    started = time.perf_counter()
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    for group in groups:
        log = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".log")
        logs.append(log.name)
        procs.append(subprocess.Popen([python, "-m", "unittest", *group], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env))
    status = 0
    for proc in procs:
        status = proc.wait() or status
    texts = [pathlib.Path(name).read_text(errors="replace") for name in logs]
    for name in logs:
        os.unlink(name)
    ran = sum(int(m.group(1)) for text in texts for m in re.finditer(r"^Ran (\d+) tests?", text, re.M))
    failing = sorted({re.sub(r" \(.*", "", line) for text in texts for line in text.splitlines() if line.startswith(("FAIL:", "ERROR:"))})
    if status:
        for text in texts:
            print(text[-3000:])
    for name in failing:
        print("test.sh: failing: " + name)
    print("test.sh: %d tests ran, exit %d, %d s (quick: %d modules in %d processes; CI runs the full suite)" % (ran, status, round(time.perf_counter() - started), len(modules), len(groups)))
    return status


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] not in ("select", "run") or "--base" not in argv:
        print("usage: python -m tests.quickselect select|run --base BASE [--jobs N]", file=sys.stderr)
        return 2
    base = argv[argv.index("--base") + 1]
    jobs = int(argv[argv.index("--jobs") + 1]) if "--jobs" in argv else min(os.cpu_count() or 2, MAX_JOBS)
    modules = quick_targets(changed_files(base))
    if argv[0] == "select":
        print("\n".join(modules))
        return 0
    return run(modules, jobs)


if __name__ == "__main__":
    sys.exit(main())
