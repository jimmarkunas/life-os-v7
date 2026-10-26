"""Run one shard of the unit-test suite: `python -m tests.shard K N` runs shard K of N (1-based).

The PR check runs the shards side by side in one job so the suite finishes in about half the time (Jim, Oct 26 2026: the suite had reached the 75 s limit). A shard is
a set of whole TEST MODULES (module-level fixtures keep working), balanced by the size of each module's source file (longest first, to the lightest shard); the split is
deterministic for a given tree. tests/contracts/test_shards.py proves every test runs in exactly one shard. A module that failed to import always lands in shard 1, so
the failure is reported once and never lost."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def tests_of(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from tests_of(item)
        else:
            yield item


def module_of(test):
    return test.__class__.__module__ if test.__class__.__name__ != "_FailedTest" else None


def assignment(modules, n):
    """{module: shard index 0..n-1}: longest source file first, each to the currently lightest shard (ties to the lowest index)."""
    def size(name):
        path = ROOT / (name.replace(".", "/") + ".py")
        return path.stat().st_size if path.exists() else 0
    load, out = [0] * n, {}
    for name in sorted(modules, key=lambda m: (-size(m), m)):
        i = load.index(min(load))
        out[name] = i
        load[i] += size(name) or 1
    return out


def shard_suite(k, n, loader=None):
    loader = loader or unittest.defaultTestLoader
    everything = list(tests_of(loader.discover("tests", top_level_dir=str(ROOT))))
    where = assignment({module_of(t) for t in everything if module_of(t)}, n)
    keep = [t for t in everything if (0 if module_of(t) is None else where[module_of(t)]) == k - 1]
    return unittest.TestSuite(keep), len(everything)


def main(argv=None):
    k, n = (int(x) for x in (argv if argv is not None else sys.argv[1:]))
    if not 1 <= k <= n:
        raise SystemExit("usage: python -m tests.shard K N   (1 <= K <= N)")
    suite, total = shard_suite(k, n)
    print(f"shard {k}/{n}: {suite.countTestCases()} of {total} tests")
    return 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
