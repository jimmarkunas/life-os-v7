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


# Measured seconds for the modules that take 1.5 s or more (LIFE-406, Nov 8 2026: one process, the whole suite, 138 s in all). File size alone put the slow ones
# together and a shard passed the 75 s limit. Every other module is weighted by file size, scaled so that all of them together come to the 31 s they measured.
# Refresh these when a shard nears the limit.
SECONDS = {
    "tests.buzz.test_remote_mcp": 17.7,
    "tests.contracts.test_readiness": 11.7,
    "tests.edge.botstudio.test_install": 9.7,
    "tests.jobs.fit.test_backlog": 7.0,
    "tests.hermes.test_connector_allowlist": 5.9,
    "tests.buzz.test_mentions": 5.7,
    "tests.edge.backup.test_offsite_backup": 4.9,
    "tests.jobs.fit.test_backlog_workflow": 4.6,
    "tests.buzz.test_contract": 4.3,
    "tests.edge.buzzguard.test_install": 4.2,
    "tests.platform.test_decisions": 4.0,
    "tests.imessage.test_relay": 3.6,
    "tests.hermes.test_avatars": 3.3,
    "tests.jobs.fit.test_cloud": 2.8,
    "tests.buzz.test_bridge": 2.7,
    "tests.edge.openclaw.test_deploy_avatar": 2.1,
    "tests.contracts.test_workflow": 1.9,
    "tests.jobs.fit.test_stage_chunks": 1.8,
    "tests.edge.runner.test_runner": 1.7,
    "tests.jira.test_readiness_mcp": 1.5,
    "tests.contracts.test_privacy": 1.5,
}
REST_SECONDS = 31.0


def assignment(modules, n):
    """{module: shard index 0..n-1}: heaviest first (measured seconds, else a size estimate), each to the currently lightest shard (ties to the lowest index)."""
    def kb(name):
        path = ROOT / (name.replace(".", "/") + ".py")
        return path.stat().st_size / 1024 if path.exists() else 0.0
    rest = sum(kb(m) for m in modules if m not in SECONDS) or 1.0
    scale = REST_SECONDS / rest
    def weight(name):
        return SECONDS[name] if name in SECONDS else kb(name) * scale
    load, out = [0.0] * n, {}
    for name in sorted(modules, key=lambda m: (-weight(m), m)):
        i = load.index(min(load))
        out[name] = i
        load[i] += weight(name) or 0.001
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
