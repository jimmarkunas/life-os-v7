"""Run one shard of the unit-test suite: `python -m tests.shard K N` runs shard K of N (1-based).

The PR check runs the shards side by side in one job so the suite finishes in about half the time (Jim, Oct 26 2026: the suite had reached the 75 s limit). A shard is
a set of whole TEST MODULES (module-level fixtures keep working), balanced by each module's MEASURED seconds (tests/shard-weights.json, LIFE-557; a module the file does
not list is weighted by its source size) - longest first, to the lightest shard; the split is deterministic for a given tree. `python -m tests.shard --measure` rewrites the file
from a full green run (fastest of two runs per module). tests/contracts/test_shards.py proves every test runs in exactly one shard. A module that failed to import always lands in shard 1, so
the failure is reported once and never lost."""
import io
import json
import os
import pathlib
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEIGHTS = ROOT / "tests" / "shard-weights.json"       # LIFE-557: measured seconds per test module; written by `python -m tests.shard --measure`


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


def load_weights(path=None):
    """{module: seconds} from tests/shard-weights.json, or {} when the file is missing or unreadable (the split then uses the older measured table and file sizes)."""
    try:
        data = json.loads(pathlib.Path(path or WEIGHTS).read_text())
        return {str(k): float(v) for k, v in data["seconds"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def assignment(modules, n, weights=None):
    """{module: shard index 0..n-1}: heaviest first, each to the currently lightest shard (ties to the lowest index).
    A module's weight is its measured seconds from the weights file; a module the file does not list is weighted by its source size at the average seconds per kilobyte of the
    listed ones, so a new module never breaks the split. Without a weights file the older table (SECONDS) and the 31 s size scale apply, as before."""
    weights = load_weights() if weights is None else weights
    def kb(name):
        path = ROOT / (name.replace(".", "/") + ".py")
        return path.stat().st_size / 1024 if path.exists() else 0.0
    if weights:
        listed = [m for m in modules if m in weights]
        rate = (sum(weights[m] for m in listed) / (sum(kb(m) for m in listed) or 1.0)) if listed else 0.01
        def weight(name):
            return weights[name] if name in weights else kb(name) * rate
    else:
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


def check_weights(weights, known_modules):
    """-> list of problems: a module that does not exist, a non-positive or non-numeric time. Used by the contract test."""
    problems = []
    for name, seconds in sorted(weights.items()):
        if name not in known_modules:
            problems.append(f"{name} is listed but no such test module exists")
        elif not isinstance(seconds, (int, float)) or seconds <= 0:
            problems.append(f"{name} has a bad time")
    return problems


def modules_in(tests):
    out = {}
    for test in tests:
        name = module_of(test)
        if name:
            out.setdefault(name, []).append(test)
    return out


def measure(tests, clock=time.perf_counter):
    """{module: seconds} from ONE pass: each module's tests run as their own suite (so its module fixtures count), modules in the order a full run uses. A second pass in the same
    process is not possible (some modules keep state between runs), so a pass is its own process; `merge` keeps the fastest of several."""
    seconds = {}
    for name, group in modules_in(tests).items():                      # discovery order, exactly as a full run goes (some modules lean on state an earlier one set)
        started = clock()
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(unittest.TestSuite(group))
        took = clock() - started
        if not result.wasSuccessful():
            raise SystemExit(f"{name} failed; weights come only from a green run")
        seconds[name] = took
    return seconds


def merge(passes):
    """Fastest time per module over several passes (a busy machine only ever adds time), rounded."""
    best = {}
    for one in passes:
        for name, seconds in one.items():
            best[name] = min(seconds, best.get(name, seconds))
    return {name: round(seconds, 2) for name, seconds in sorted(best.items())}


class TimedResult(unittest.TextTestResult):
    """LIFE-705: wall seconds per test module inside this shard (module and class fixtures included, so the sum is the shard's time). Tests of a module run one after another, so a module's
    time is the span from its first test starting to the next module's first test starting (the last one: to the end of the run)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.module_seconds, self._module, self._since = {}, None, 0.0

    def _close(self, now):
        if self._module is not None:
            self.module_seconds[self._module] = self.module_seconds.get(self._module, 0.0) + (now - self._since)

    def startTest(self, test):
        module, now = module_of(test) or "", time.perf_counter()
        if module != self._module:
            self._close(now)
            self._module, self._since = module, now
        super().startTest(test)

    def stopTestRun(self):
        self._close(time.perf_counter())
        self._module = None
        super().stopTestRun()


def timing_report(k, n, seconds, floor=0.05, top=12):
    """The lines a shard prints after its run: the slowest modules for people, and one `shard-seconds {...}` line that `--from-logs` turns into weights (modules under `floor` seconds are summed as 'rest')."""
    kept = {m: round(v, 2) for m, v in sorted(seconds.items()) if v >= floor and m}
    rest = round(sum(seconds.values()) - sum(kept.values()), 2)
    slow = ", ".join(f"{m.rsplit('.', 1)[-1]} {v:.1f}s" for m, v in sorted(kept.items(), key=lambda kv: -kv[1])[:top])
    return [f"shard {k}/{n} took {sum(seconds.values()):.1f} s; slowest modules: {slow}", "shard-seconds " + json.dumps({"k": k, "n": n, "seconds": kept, "rest": rest}, sort_keys=True)]


def weights_from_logs(texts, known_modules):
    """{module: seconds}: the mean over the given shard logs of each module's `shard-seconds` time, and 0.01 for a known module no log shows (it ran in under 0.05 s everywhere)."""
    seen = {}
    for text in texts:
        for line in text.splitlines():
            if line.startswith("shard-seconds "):
                for module, seconds in json.loads(line[len("shard-seconds "):])["seconds"].items():
                    seen.setdefault(module, []).append(seconds)
    out = {m: sum(v) / len(v) for m, v in seen.items() if m in known_modules}
    for module in known_modules:
        out.setdefault(module, 0.01)
    return out


def write_weights(seconds, path=None):
    """Deterministic file: module names sorted, seconds rounded, no dates, no paths."""
    pathlib.Path(path or WEIGHTS).write_text(json.dumps({"v": 1, "seconds": {k: max(round(v, 2), 0.01) for k, v in sorted(seconds.items())}}, indent=1) + "\n")     # a module that ran in under 5 ms is listed at 0.01 s: a time of 0 would read as a bad entry


def shard_suite(k, n, loader=None):
    loader = loader or unittest.defaultTestLoader
    everything = list(tests_of(loader.discover("tests", top_level_dir=str(ROOT))))
    where = assignment({module_of(t) for t in everything if module_of(t)}, n)
    keep = [t for t in everything if (0 if module_of(t) is None else where[module_of(t)]) == k - 1]
    return unittest.TestSuite(keep), len(everything)


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv[:1] == ["--measure-pass"]:                         # one timed pass in this process; the raw seconds go to the file named next
        os.environ["SHARD_MEASURING"] = "1"
        everything = list(tests_of(unittest.defaultTestLoader.discover("tests", top_level_dir=str(ROOT))))
        pathlib.Path(argv[1]).write_text(json.dumps(measure(everything)))
        return 0
    if argv[:1] == ["--measure"]:
        import subprocess
        import tempfile
        runs = int(argv[2]) if argv[1:2] == ["--runs"] else 2
        passes = []
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(runs):
                out = pathlib.Path(tmp) / f"pass{i}.json"
                if subprocess.run([sys.executable, "-m", "tests.shard", "--measure-pass", str(out)], cwd=ROOT).returncode != 0:
                    return 1
                passes.append(json.loads(out.read_text()))
        seconds = merge(passes)
        write_weights(seconds)
        print(f"wrote {WEIGHTS.relative_to(ROOT)}: {len(seconds)} modules, {sum(seconds.values()):.0f} s in all (fastest of {runs} passes)")
        return 0
    if argv[:1] == ["--from-logs"]:                              # LIFE-705: weights from CI shard logs: python -m tests.shard --from-logs shard-1.log shard-2.log ...
        everything = list(tests_of(unittest.defaultTestLoader.discover("tests", top_level_dir=str(ROOT))))
        seconds = weights_from_logs([pathlib.Path(name).read_text() for name in argv[1:]], set(modules_in(everything)))
        write_weights(seconds)
        print(f"wrote {WEIGHTS.relative_to(ROOT)}: {len(seconds)} modules, {sum(seconds.values()):.0f} s in all (mean of {len(argv) - 1} shard logs)")
        return 0
    k, n = (int(x) for x in argv)
    if not 1 <= k <= n:
        raise SystemExit("usage: python -m tests.shard K N   (1 <= K <= N)   |   python -m tests.shard --measure   |   python -m tests.shard --from-logs LOG...")
    suite, total = shard_suite(k, n)
    print(f"shard {k}/{n}: {suite.countTestCases()} of {total} tests")
    result = unittest.TextTestRunner(verbosity=1, resultclass=TimedResult).run(suite)
    for line in timing_report(k, n, result.module_seconds):
        print(line)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
