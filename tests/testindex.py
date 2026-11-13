"""LIFE-548: a test index (which tests run which production file) and a selector that picks the tests a change can affect.

  python -m tests.testindex build [--out docs/test-index.json]     run the whole suite once with per-test coverage contexts and write the index (needs `coverage`; about 3 minutes). The index is NOT committed (LIFE-644):
                                                                   CI builds it on every push to main and saves it in the Actions cache; pull requests restore it. A local one is git-ignored.
  python -m tests.testindex select --base HEAD^1                   print the unittest targets for the files changed since BASE; the single word FULL means "run everything"
  python -m tests.testindex check [--max-age-days 7]               exit 1 (with the reason) when the index is older than that or unreadable

Safety first: when in doubt the selector says FULL: no index, an unreadable one, one older than 7 days, or any trouble reading it. tests/contracts ALWAYS runs. Changes to the shared test kit, the test tooling, workflows, locks, config, the shared
platform layer, privacy files, or any file the selector does not know are FULL. A production file missing from the index falls back to its mirror test folder (tests/<package>),
and with no mirror to FULL. Docs-only changes run just the contracts. The index holds only repo-relative paths and dotted test ids: no data, no timestamps but one
`generated_at`, no absolute paths. The full suite still runs on every push to main and every hourly run (the CI wiring is in docs/platform/test-index.md)."""
import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "test-index.json"
SCHEMA = 1
MAX_AGE_DAYS = 7

FULL = "FULL"
FULL_PREFIXES = ("tests/kit/", ".github/", ".claude/", "lifeos/platform/", "requirements")      # a change here can affect any test
FULL_FILES = ("docs/PRIVACY.md", "pyproject.toml", "setup.cfg", "setup.py", "tox.ini", ".coveragerc", "conftest.py", ".gitignore")
FULL_SUFFIXES = (".lock",)
IGNORED_PREFIXES = ("docs/", "tools/")                                                         # docs run the contracts only; tools/ has its own node tests (separate CI step)


def dotted(path):
    return path[:-3].replace("/", ".")


def test_modules(root=ROOT, folder="tests"):
    """-> sorted dotted module names of every test_*.py under `folder` (repo-relative)."""
    base = pathlib.Path(root) / folder
    return sorted(dotted(str(p.relative_to(root))) for p in base.rglob("test_*.py") if p.is_file())


def production_files(root=ROOT):
    """-> sorted lifeos/ python files (relative to lifeos/), without package markers."""
    base = pathlib.Path(root) / "lifeos"
    return sorted(str(p.relative_to(base)) for p in base.rglob("*.py") if p.name != "__init__.py")


def build(coverage_json, tests, skipped=(), commit="", now=None):
    """coverage_json: coverage.py's JSON with show_contexts; tests: all test ids on disk -> the index dict (pure; deterministic apart from generated_at)."""
    ids = sorted(set(tests))
    position = {t: i for i, t in enumerate(ids)}
    covers = {}
    for path, data in coverage_json.get("files", {}).items():
        if not path.startswith("lifeos/") or path.endswith("__init__.py"):
            continue
        found = set()
        for contexts in (data.get("contexts") or {}).values():
            for context in contexts:
                if context in position:
                    found.add(position[context])
        covers[path[len("lifeos/"):]] = sorted(found)
    modules = {}
    for t in ids:
        parts = t.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            candidate = "/".join(parts[:cut]) + ".py"
            if (ROOT / candidate).is_file():
                modules[".".join(parts[:cut])] = candidate
                break
    return {
        "schema": SCHEMA,
        "generated_at": (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": commit,
        "tests": ids,
        "modules": dict(sorted(modules.items())),
        "skipped": sorted(position[t] for t in skipped if t in position),
        "covers": dict(sorted(covers.items())),
        "uncovered": sorted(p for p in production_files() if p not in covers or not covers[p]),
    }


def dump(index):
    """One line per production file so a regenerated index reads as a small diff."""
    lines = ["{"]
    for key in ("schema", "generated_at", "commit"):
        lines.append("  %s: %s," % (json.dumps(key), json.dumps(index[key])))
    lines.append('  "tests": %s,' % json.dumps(index["tests"], separators=(",", ":")))
    lines.append('  "modules": %s,' % json.dumps(index["modules"], sort_keys=True, separators=(",", ":")))
    lines.append('  "skipped": %s,' % json.dumps(index["skipped"], separators=(",", ":")))
    lines.append('  "uncovered": %s,' % json.dumps(index["uncovered"], separators=(",", ":")))
    lines.append('  "covers": {')
    items = list(index["covers"].items())
    for i, (path, nums) in enumerate(items):
        lines.append("    %s: %s%s" % (json.dumps(path), json.dumps(nums, separators=(",", ":")), "," if i < len(items) - 1 else ""))
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def load(path=INDEX):
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def contract_modules(root=ROOT):
    return test_modules(root, "tests/contracts")


def select(changed, index, root=ROOT):
    """-> FULL, or a sorted list of unittest targets (dotted modules, or dotted test ids). When in doubt FULL."""
    if index is None:
        return FULL
    changed = [c for c in changed if c]
    targets = set(contract_modules(root))
    ids = index["tests"]
    for path in changed:
        if path in FULL_FILES or path.endswith(FULL_SUFFIXES) or path.startswith(FULL_PREFIXES):
            return FULL
        if "/" not in path and not path.endswith(".md"):                       # a top-level file we do not know (build/config files, scripts)
            return FULL
        if path.startswith("tests/") and "/" not in path[len("tests/"):]:      # tests/__init__.py, tests/shard.py, tests/testindex.py: the harness itself
            if path.startswith("tests/test_") and path.endswith(".py"):
                targets.add(dotted(path))
                continue
            return FULL
        if path.startswith("tests/") and path.endswith(".py"):
            if os.path.basename(path).startswith("test_"):
                if (pathlib.Path(root) / path).is_file():
                    targets.add(dotted(path))
                continue
            return FULL                                                        # a helper under tests/ (support module): it can serve any test
        if path.startswith(IGNORED_PREFIXES) or path.endswith(".md"):
            continue
        if path.startswith("lifeos/"):
            rel = path[len("lifeos/"):]
            package = rel.split("/")[0] if "/" in rel else None
            mirror = pathlib.Path(root) / "tests" / package if package else None
            stem = os.path.splitext(os.path.basename(rel))[0]
            if rel.endswith(".py") and rel in index["covers"] and index["covers"][rel]:
                targets.update(ids[i] for i in index["covers"][rel])
                if mirror and mirror.is_dir():                                 # an index can be days old: the tests named after this module always run too
                    targets.update(dotted(str(p.relative_to(root))) for p in mirror.rglob("test_%s*.py" % stem))
                elif not package and (pathlib.Path(root) / "tests" / ("test_%s.py" % stem)).is_file():
                    targets.add("tests.test_%s" % stem)
                continue
            if package and mirror.is_dir():                                    # unknown to the index: the whole mirror folder
                targets.update(test_modules(root, "tests/" + package))
                continue
            if not package and (pathlib.Path(root) / "tests" / ("test_%s.py" % stem)).is_file():
                targets.add("tests.test_%s" % stem)
                continue
            return FULL
        return FULL
    modules = {t for t in targets if t in module_set(root)}
    return sorted(t for t in targets if t in modules or not any(t.startswith(m + ".") for m in modules))     # a test id inside a selected module would run twice


def module_set(root=ROOT):
    key = str(root)
    if key not in _MODULES:
        _MODULES[key] = set(test_modules(pathlib.Path(root)))
    return _MODULES[key]


_MODULES = {}


def check(index, now=None, max_age_days=MAX_AGE_DAYS):
    """-> list of problems (empty = usable): unreadable, or too old. A production file the index does not list (new, renamed or deleted since the build) is NOT a problem (LIFE-588): `select` sends it to its
    mirror test folder, or to FULL, so adding a source never needs a rebuild of the index."""
    if index is None:
        return ["the test index is missing or unreadable"]
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        made = datetime.datetime.strptime(index["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        age = (now - made).days
        return ["the test index is %d days old (limit %d)" % (age, max_age_days)] if age > max_age_days else []
    except (KeyError, ValueError):
        return ["the test index has no readable generated_at"]


def all_tests(root=ROOT):
    """-> (every test id, the skipped ones), found by loading the suite the same way CI does."""
    suite = unittest.defaultTestLoader.discover(str(pathlib.Path(root) / "tests"), pattern="test_*.py", top_level_dir=str(root))
    found, skipped = [], []

    def walk(item):
        if isinstance(item, unittest.TestSuite):
            for sub in item:
                walk(sub)
        else:
            tid = item.id()
            found.append(tid)
            method = getattr(item, item._testMethodName, None) if hasattr(item, "_testMethodName") else None
            if getattr(item.__class__, "__unittest_skip__", False) or getattr(method, "__unittest_skip__", False):
                skipped.append(tid)
    walk(suite)
    return found, skipped


def changed_files(base):
    out = subprocess.run(["git", "diff", "--name-only", base, "HEAD"], capture_output=True, text=True, cwd=ROOT, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def build_command(out):
    """Run the whole suite under coverage with one context per test, then write the index. Needs coverage.py installed."""
    rc = ROOT / ".coverage-test-index.rc"
    data = ROOT / ".coverage-test-index.data"
    rc.write_text("[run]\ndynamic_context = test_function\nsource = lifeos\n")
    try:
        subprocess.run([sys.executable, "-m", "coverage", "run", "--rcfile=%s" % rc, "--data-file=%s" % data, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-p", "test_*.py"], cwd=ROOT, check=True,
                       env={**os.environ, "TESTINDEX_BUILDING": "1"})     # the guards that compare the OLD index with the code are skipped while the new one is being written
        subprocess.run([sys.executable, "-m", "coverage", "json", "--rcfile=%s" % rc, "--data-file=%s" % data, "--show-contexts", "-o", str(ROOT / ".coverage-test-index.json"), "-q"], cwd=ROOT, check=True)
        coverage_json = json.loads((ROOT / ".coverage-test-index.json").read_text())
    finally:
        for leftover in (rc, data, ROOT / ".coverage-test-index.json"):
            leftover.unlink(missing_ok=True)
    tests, skipped = all_tests()
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    index = build(coverage_json, tests, skipped, commit)
    pathlib.Path(out).write_text(dump(index))
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m tests.testindex")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", default=str(INDEX))
    s = sub.add_parser("select")
    s.add_argument("--base", default="HEAD^1")
    c = sub.add_parser("check")
    c.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    args = parser.parse_args(argv)
    if args.command == "build":
        index = build_command(args.out)
        print("test index: %d tests, %d production files, %d uncovered" % (len(index["tests"]), len(index["covers"]), len(index["uncovered"])))
        return 0
    if args.command == "select":
        try:
            index = load()
            picked = select(changed_files(args.base), None if check(index) else index)     # an index older than 7 days is never used
        except Exception:                                                       # cannot list the change, or the index is damaged in any way: run everything
            picked = FULL
        print(FULL if picked == FULL else "\n".join(picked))
        return 0
    problems = check(load(), max_age_days=args.max_age_days)
    for problem in problems:
        print("TEST_INDEX_STALE: " + problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
