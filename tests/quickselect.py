"""The quick local gate (LIFE-763 CI speed): `python -m tests.quickselect run --base BASE` runs, in parallel processes, the tests a push most often needs: every contract test, the repo-wide
scanners (testindex.REPO_WIDE), the tests that name or mirror each changed file, the tests that reach a changed lifeos module through the import graph (a test of `credentials` reaches `credential_list` because `credentials` imports it), the tests that
read a changed data file by path, and any changed test. It never escalates to the whole suite: CI runs that (Jim, Nov 16 2026). `select` only prints the targets."""
import ast
import functools
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
HUB_CLOSURE = 40                                                            # a module that more than this many lifeos modules reach is a hub (db, http): follow its direct importers only, never the whole tree


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


@functools.lru_cache(maxsize=4)
def lifeos_modules(root=ROOT):
    """{dotted name: path} for every module under lifeos/ (a package's __init__ is the package)."""
    found = {}
    for path in pathlib.Path(root, "lifeos").rglob("*.py"):
        name = ".".join(path.relative_to(root).with_suffix("").parts)
        found[name[: -len(".__init__")] if name.endswith(".__init__") else name] = path
    return found


def _imported(tree, package, known):
    """The lifeos modules a parsed file imports, lazy imports inside functions included (`from a.b import c` names `a.b` and, when it exists, `a.b.c`)."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(parts + ([node.module] if node.module else []))
            found.add(base)
            found |= {base + "." + a.name for a in node.names}
    return {m for m in found if m in known}


@functools.lru_cache(maxsize=4)
def reverse_graph(root=ROOT):
    """{lifeos module: the lifeos modules that import it}, from the AST of lifeos/ (a name in an __all__ list is not an import)."""
    known = lifeos_modules(root)
    reverse = {}
    for name, path in known.items():
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except SyntaxError:
            continue
        for dep in _imported(tree, package, known):
            if dep != name:
                reverse.setdefault(dep, set()).add(name)
    return reverse


def reached(modules, root=ROOT):
    """The changed lifeos modules plus every lifeos module that imports one of them, transitively. A hub (reached by more than HUB_CLOSURE modules) contributes its direct importers only."""
    reverse = reverse_graph(root)
    out = set(modules)
    for module in modules:
        seen, frontier = {module}, {module}
        while frontier:
            frontier = {x for f in frontier for x in reverse.get(f, ())} - seen
            seen |= frontier
        out |= seen if len(seen) <= HUB_CLOSURE else {module} | reverse.get(module, set())
    return out


@functools.lru_cache(maxsize=4)
def test_texts(root=ROOT):
    """{dotted test module: source text}, read once per run."""
    texts = {}
    for path in pathlib.Path(root, "tests").rglob("test_*.py"):
        try:
            texts[testindex.dotted(str(path.relative_to(root)))] = path.read_text(errors="replace")
        except OSError:
            continue
    return texts


@functools.lru_cache(maxsize=4)
def test_references(root=ROOT):
    """{dotted test module: the lifeos modules it imports or names in a string (a mock.patch target)}."""
    known = lifeos_modules(root)
    out = {}
    for name, text in test_texts(root).items():
        found = set()
        try:
            tree = ast.parse(text)
            found |= _imported(tree, name.rpartition(".")[0], known)
        except SyntaxError:
            pass
        for dotted in re.findall(r"\blifeos(?:\.\w+)+", text):
            parts = dotted.split(".")
            while parts and ".".join(parts) not in known:
                parts.pop()
            if parts:
                found.add(".".join(parts))
        out[name] = found
    return out


def reaching_tests(changed_modules, root=ROOT):
    """Test modules that import or name a changed lifeos module or anything that imports one (the tests of `credentials` for a change to `credential_list`)."""
    wanted = reached(changed_modules, root)
    return {name for name, refs in test_references(root).items() if refs & wanted}


def data_readers(path, root=ROOT):
    """Test modules whose source names this changed non-Python file by its repo path, or by its file name when that is distinctive (`codes.json`)."""
    base = pathlib.Path(path).name
    needles = {path} | ({base} if "." in base and len(base) >= 8 else set())
    return {name for name, text in test_texts(root).items() if any(n in text for n in needles)}


def quick_targets(changed, root=ROOT):
    """-> sorted dotted test modules: contracts, the repo-wide scanners, and what each changed file maps to. Pure apart from reading the tree."""
    known = testindex.module_set(root)
    targets = set(testindex.contract_modules(root)) | {m for m in testindex.REPO_WIDE if m in known}
    modules = lifeos_modules(root)
    changed_modules = {path[:-3].replace("/", ".") for path in changed if path.startswith("lifeos/") and path.endswith(".py") and pathlib.Path(path).name != "__init__.py"}
    targets |= reaching_tests({m for m in changed_modules if m in modules}, root)
    for path in changed:
        if not path.endswith(".py") and (path.startswith(("lifeos/", "tests/", "tools/", "docs/", ".github/")) or "/" not in path):
            targets |= data_readers(path, root)
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
