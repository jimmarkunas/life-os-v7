"""a test index (which tests run which production file) and a selector that picks the tests a change can affect."""

import argparse
import datetime
import functools
import json
import re
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
FULL_PREFIXES = (
    "tests/kit/",
    ".github/",
    ".claude/",
    "requirements",
)  # a change here can affect any test
INDEX_FULL_PREFIXES = ("lifeos/platform/",)  # the coverage index cannot see what a hub reaches; the import graph can
FULL_FILES = (
    "docs/PRIVACY.md",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    ".coveragerc",
    "conftest.py",
    ".gitignore",
)
FULL_SUFFIXES = (".lock",)
REPO_WIDE = (
    "tests.platform.test_docstyle",
    "tests.platform.test_settings",
    "tests.platform.test_bws_plain",
    "tests.platform.test_switches",
    "tests.platform.test_due",
    "tests.jira.test_jira",
)
IGNORED_PREFIXES = ("docs/", "tools/")  # docs and tools run the contracts and the tests that name the file
DATA_SUFFIXES = (
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".txt",
    ".sh",
    ".toml",
    ".csv",
    ".html",
    ".js",
    ".mjs",
    ".cfg",
    ".ini",
)
SOURCE_ROOTS = ("lifeos/", "tests/", "tools/", "docs/")


def dotted(path):
    return path[:-3].replace("/", ".")


def test_modules(root=ROOT, folder="tests"):
    """> sorted dotted module names of every test_*.py under `folder` (repo-relative)."""
    base = pathlib.Path(root) / folder
    return sorted(dotted(str(p.relative_to(root))) for p in base.rglob("test_*.py") if p.is_file())


def production_files(root=ROOT):
    """> sorted lifeos/ python files (relative to lifeos/), without package markers."""
    base = pathlib.Path(root) / "lifeos"
    return sorted(str(p.relative_to(base)) for p in base.rglob("*.py") if p.name != "__init__.py")


def build(coverage_json, tests, skipped=(), commit="", now=None):
    """coverage_json: coverage.py's JSON with show_contexts"""
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
        covers[path[len("lifeos/") :]] = sorted(found)
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
        lines.append(
            "    %s: %s%s"
            % (json.dumps(path), json.dumps(nums, separators=(",", ":")), "," if i < len(items) - 1 else "")
        )
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


@functools.lru_cache(maxsize=4)
def test_texts(root=ROOT):
    """{dotted test module: source text}, read once per run."""
    texts = {}
    for path in pathlib.Path(root, "tests").rglob("test_*.py"):
        try:
            texts[dotted(str(path.relative_to(root)))] = path.read_text(errors="replace")
        except OSError:
            continue
    return texts


def importers(dotted_name, stem, root=ROOT):
    """Test modules whose text names the production module (`lifeos.pkg.stem`, or `from lifeos.pkg import stem`)."""
    package = dotted_name.rsplit(".", 1)[0]
    pattern = re.compile(
        r"\b%s\b|from\s+%s\s+import[^\n]*\b%s\b" % (re.escape(dotted_name), re.escape(package), re.escape(stem))
    )
    return {name for name, text in test_texts(root).items() if pattern.search(text)}


def data_readers(path, root=ROOT):
    """Test modules whose source names this changed file by its repo path, or by its file name (or, for a script, its
    stem) when that is distinctive (`codes.json`, `ci_failure_summary`).
    """
    base = pathlib.Path(path).name
    stem = pathlib.Path(path).stem
    needles = {path} | ({base} if "." in base and len(base) >= 8 else set()) | ({stem} if len(stem) >= 12 else set())
    return {name for name, text in test_texts(root).items() if any(n in text for n in needles)}


def _mirror(rel, package, stem, root, whole):
    """Test modules that mirror a lifeos file: its package's test folder (the whole folder when `whole`, else the tests
    named after the file) or, at the top level, tests/test_<stem>.py.
    """
    base = pathlib.Path(root) / "tests"
    if package and (base / package).is_dir():
        if whole:
            return set(test_modules(root, "tests/" + package))
        return {dotted(str(p.relative_to(root))) for p in (base / package).rglob("test_%s*.py" % stem)}
    if not package and (base / ("test_%s.py" % stem)).is_file():
        return {"tests.test_%s" % stem}
    return set()


def _targets_for(path, index, reaching, root):
    """> FULL, or the set of test modules and ids one changed path calls for beyond the contracts. Anything this cannot
    place is FULL.
    """
    root = pathlib.Path(root)
    name = os.path.basename(path)
    full_prefixes = FULL_PREFIXES + (INDEX_FULL_PREFIXES if reaching is None else ())
    if path in FULL_FILES or path.endswith(FULL_SUFFIXES) or path.startswith(full_prefixes):
        return FULL
    suffix = os.path.splitext(name)[1]
    if "/" not in path:
        return set() if path.endswith(".md") else FULL
    if path.startswith("tests/"):
        if path.endswith(".py"):
            if not name.startswith("test_"):
                return FULL  # a helper, the shard runner or the selector itself: anything may use it
            return {dotted(path)} if (root / path).is_file() else set()
        return data_readers(path, root) or FULL
    if path.startswith(IGNORED_PREFIXES):
        if suffix not in DATA_SUFFIXES and suffix != ".py":
            return FULL
        return data_readers(path, root)
    if path.startswith("lifeos/"):
        rel = path[len("lifeos/") :]
        package = rel.split("/")[0] if "/" in rel else None
        stem = os.path.splitext(name)[0]
        found = set()
        if suffix == ".py":
            if name == "__init__.py":
                return _mirror(rel, package, stem, root, True) or FULL
            module = path[:-3].replace("/", ".")
            ids = index["tests"] if index else []
            covered = {ids[i] for i in index["covers"].get(rel, ())} if index else set()
            found |= covered
            if reaching is not None:  # the local gate: the import graph and every test that names the module
                found |= reaching({module}) | importers(module, stem, root)
            whole = not covered and package != "platform"
            found |= _mirror(rel, package, stem, root, whole)
        elif suffix in DATA_SUFFIXES:
            found |= data_readers(path, root) | _mirror(rel, package, stem, root, True)
        return found or FULL
    return FULL


def select(changed, index, root=ROOT, reaching=None):
    """> FULL, or a sorted list of unittest targets (dotted modules, or dotted test ids). The ONE selector: PR CI calls it
    with the coverage `index`, the local gate with `reaching` (a function from changed lifeos modules to the test modules
    that reach them through the import graph). With neither it cannot judge, and says FULL; a path it cannot place is
    FULL too.
    """
    if index is None and reaching is None:
        return FULL
    known = module_set(root)
    targets = set(contract_modules(root)) | {m for m in REPO_WIDE if m in known}
    for path in (c for c in changed if c):
        found = _targets_for(path, index, reaching, root)
        if found == FULL:
            return FULL
        targets |= found
    targets = {t for t in targets if t in known or any(t.startswith(m + ".") for m in known)}
    targets = {_current(t, known, root) for t in targets}  # a renamed test method runs as its module
    modules = {t for t in targets if t in known}
    return sorted(
        t for t in targets if t in modules or not any(t.startswith(m + ".") for m in modules)
    )  # a test id inside a selected module would run twice


def _current(target, known, root=ROOT):
    """> the target when it is a module or a test id whose method still exists in its file"""
    if target in known:
        return target
    module = max((m for m in known if target.startswith(m + ".")), key=len)
    method = target.rsplit(".", 1)[-1]
    try:
        text = (pathlib.Path(root) / (module.replace(".", "/") + ".py")).read_text()
    except OSError:
        return module
    return target if re.search(r"def %s\(" % re.escape(method), text) else module


def module_set(root=ROOT):
    key = str(root)
    if key not in _MODULES:
        _MODULES[key] = set(test_modules(pathlib.Path(root)))
    return _MODULES[key]


_MODULES = {}


def check(index, now=None, max_age_days=MAX_AGE_DAYS):
    """> list of problems (empty = usable): unreadable, or too old."""
    if index is None:
        return ["the test index is missing or unreadable"]
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        made = datetime.datetime.strptime(index["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
        age = (now - made).days
        return ["the test index is %d days old (limit %d)" % (age, max_age_days)] if age > max_age_days else []
    except (KeyError, ValueError):
        return ["the test index has no readable generated_at"]


def all_tests(root=ROOT):
    """> (every test id, the skipped ones), found by loading the suite the same way CI does."""
    suite = unittest.defaultTestLoader.discover(
        str(pathlib.Path(root) / "tests"), pattern="test_*.py", top_level_dir=str(root)
    )
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


def changed_files(base, root=ROOT, local=False):
    """> the repo paths changed since `base`, a rename as its old path (deleted) plus its new one. `local` adds what is
    not committed yet: staged, unstaged and new untracked files.
    """
    commands = [["git", "diff", "--name-only", "--no-renames", base, "HEAD"]]
    if local:
        commands += [
            ["git", "diff", "--name-only", "--no-renames", "--cached"],
            ["git", "diff", "--name-only", "--no-renames"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ]
    found = []
    for command in commands:
        out = subprocess.run(command, capture_output=True, text=True, cwd=root, check=True).stdout
        found += [line.strip() for line in out.splitlines() if line.strip() and line.strip() not in found]
    return found


def build_command(out):
    """Run the whole suite under coverage with one context per test, then write the index."""
    rc = ROOT / ".coverage-test-index.rc"
    data = ROOT / ".coverage-test-index.data"
    rc.write_text("[run]\ndynamic_context = test_function\nsource = lifeos\n")
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                "--rcfile=%s" % rc,
                "--data-file=%s" % data,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-t",
                ".",
                "-p",
                "test_*.py",
            ],
            cwd=ROOT,
            check=True,
            env={**os.environ, "TESTINDEX_BUILDING": "1"},
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "json",
                "--rcfile=%s" % rc,
                "--data-file=%s" % data,
                "--show-contexts",
                "-o",
                str(ROOT / ".coverage-test-index.json"),
                "-q",
            ],
            cwd=ROOT,
            check=True,
        )
        coverage_json = json.loads((ROOT / ".coverage-test-index.json").read_text())
    finally:
        for leftover in (rc, data, ROOT / ".coverage-test-index.json"):
            leftover.unlink(missing_ok=True)
    tests, skipped = all_tests()
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT
    ).stdout.strip()
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
        print(
            "test index: %d tests, %d production files, %d uncovered"
            % (len(index["tests"]), len(index["covers"]), len(index["uncovered"]))
        )
        return 0
    if args.command == "select":
        try:
            index = load()
            picked = select(
                changed_files(args.base), None if check(index) else index
            )  # an index older than 7 days is never used
        except Exception:
            picked = FULL
        print(FULL if picked == FULL else "\n".join(picked))
        return 0
    problems = check(load(), max_age_days=args.max_age_days)
    for problem in problems:
        print("TEST_INDEX_STALE: " + problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
