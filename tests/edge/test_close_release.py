"""LIFE-47 S2 release verification and fail-closed Serve reading. A fake GitHub (URL -> bytes) only; no network."""
import json
import os
import tempfile
import unittest

from lifeos.edge.oracle import close_release as rel

C = "a" * 40
APP_SRC = b'CLOSURE = ("lifeos/hiring/close.py",)\n'
FILES = {**{p: b"# stub\n" for p in rel.EDGE}, rel.APP: APP_SRC, "lifeos/hiring/close.py": b"# close\n"}
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def real_edge(app=b"CLOSURE = ()\n"):
    """The REAL Edge closure files from this checkout plus a minimal entry point."""
    return {**{p: open(os.path.join(ROOT, p), "rb").read() for p in rel.EDGE if p != rel.APP}, rel.APP: app}


def github(files=FILES, status="ahead", tree_extra=None, mode="100644"):
    tree = [{"path": p, "type": "blob", "mode": mode, "sha": rel.blob(d)} for p, d in files.items()] + (tree_extra or [])
    urls = {f"https://api.github.com/repos/{rel.REPO}/compare/{C}...main": json.dumps({"status": status}).encode(),
            f"https://api.github.com/repos/{rel.REPO}/git/trees/{C}?recursive=1": json.dumps({"tree": tree}).encode()}
    urls.update({f"https://raw.githubusercontent.com/{rel.REPO}/{C}/{p}": d for p, d in files.items()})
    return urls.__getitem__


class Stage(unittest.TestCase):
    def dest(self):
        return os.path.join(tempfile.mkdtemp(), "tree")

    def test_the_approved_commit_stages_exactly_its_closure_and_checks_clean(self):
        d = self.dest()
        rel.stage(C, d, github(), importer=lambda p: None)
        rel.check(d)
        self.assertEqual(open(os.path.join(d, "MANIFEST")).readline().strip(), f"commit {C}")
        self.assertEqual(sorted(os.path.relpath(os.path.join(r, n), d) for r, _, ns in os.walk(d) for n in ns if n != "MANIFEST"), sorted(FILES))

    def test_every_untrusted_release_is_refused_before_anything_is_written(self):
        bad_closure = {**FILES, rel.APP: b'CLOSURE = ("../etc/passwd",)\n'}
        cases = [("NOT_A_COMMIT", "abc", github()), ("NOT_ON_MAIN", C, github(status="diverged")),
                 ("ENTRY_POINT_MISSING", C, github({k: v for k, v in FILES.items() if k != rel.APP})),
                 ("NO_CLOSURE", C, github({**FILES, rel.APP: b"x = 1\n"})), ("UNSAFE_CLOSURE", C, github(bad_closure)),
                 ("NOT_A_REGULAR_FILE", C, github(mode="120000")),                                  # a symlink in the commit
                 ("NOT_A_REGULAR_FILE", C, github({k: v for k, v in FILES.items() if k != "lifeos/hiring/close.py"}))]          # incomplete
        for code, commit, fetch in cases:
            d = self.dest()
            with self.assertRaises(rel.Refused) as caught:
                rel.stage(commit, d, fetch, importer=lambda p: None)
            self.assertTrue(str(caught.exception).startswith(code), (code, str(caught.exception)))
            self.assertFalse(os.path.exists(d))
        swapped = github()
        tampered = lambda url: b"# evil\n" if url.endswith("/lifeos/hiring/close.py") else swapped(url)        # content differs from the commit's tree
        with self.assertRaises(rel.Refused) as caught:
            rel.stage(C, self.dest(), tampered, importer=lambda p: None)
        self.assertTrue(str(caught.exception).startswith("CONTENT_MISMATCH"))

    def test_an_incomplete_import_closure_is_refused(self):
        rel.stage(C, self.dest(), github(real_edge()))                   # the real import check passes when the declared closure is complete
        leaky = real_edge(b'import lifeos.hiring.not_declared\nCLOSURE = ()\n')
        with self.assertRaises(rel.Refused) as caught:
            rel.stage(C, self.dest(), github(leaky))                     # close_app imports a module its CLOSURE does not ship
        self.assertEqual(str(caught.exception), "CLOSURE_INCOMPLETE")

    def test_the_real_import_check_leaves_the_staged_tree_exactly_as_the_manifest_says(self):
        app = open(os.path.join(ROOT, rel.APP), "rb").read()
        files = {p: open(os.path.join(ROOT, p), "rb").read() for p in rel.closure(app)}               # the real Career application and its whole closure
        d = self.dest()
        rel.stage(C, d, github(files))                                   # the real isolated import runs inside the staged tree ...
        rel.check(d)                                                     # ... and the install's very next step must still find exactly the manifest
        self.assertEqual([n for _, _, names in os.walk(d) for n in names if n.endswith((".pyc", ".pyo"))], [])
        self.assertEqual([n for _, dirs, _ in os.walk(d) for n in dirs if n == "__pycache__"], [])

    def test_check_refuses_an_extra_file_a_changed_file_or_a_symlink(self):
        for damage in (lambda d: open(os.path.join(d, "lifeos", "extra.py"), "w").write("x"),
                       lambda d: open(os.path.join(d, "lifeos", "hiring", "close.py"), "w").write("# changed"),
                       lambda d: os.symlink("/etc/passwd", os.path.join(d, "lifeos", "link"))):
            d = self.dest()
            rel.stage(C, d, github(), importer=lambda p: None)
            damage(d)
            self.assertRaises(rel.Refused, rel.check, d)


class ServeState(unittest.TestCase):
    OURS = {"TCPForward": "127.0.0.1:47344", "ProxyProtocol": 2, "TerminateTLS": "edge.example.ts.net"}

    def test_only_a_well_formed_status_can_say_free(self):
        gateway = {"TCP": {"443": {"HTTPS": True}, "47341": {"TCPForward": "127.0.0.1:47342", "ProxyProtocol": 2}}}
        cases = [("", "unknown"), ("not json", "unknown"), ("[]", "unknown"), ('{"TCP": []}', "unknown"),
                 (json.dumps(gateway), "free"), (json.dumps({"TCP": {**gateway["TCP"], "47443": self.OURS}}), "ours"),
                 (json.dumps({"TCP": {"47443": {**self.OURS, "TCPForward": "127.0.0.1:9999"}}}), "other"),              # foreign owner
                 (json.dumps({"TCP": {"47443": {"TCPForward": "127.0.0.1:47344", "ProxyProtocol": 2}}}), "other"),      # no TLS termination
                 (json.dumps({"TCP": {"47443": self.OURS}, "AllowFunnel": {"edge.example.ts.net:47443": True}}), "other"),
                 (json.dumps({"Web": {"edge.example.ts.net:47443": {}}}), "other")]
        for text, want in cases:
            self.assertEqual(rel.serve_state(text), want, text)


if __name__ == "__main__":
    unittest.main()


class IsolatedTree(unittest.TestCase):
    """The REAL Edge files, staged alone with a minimal entry point: they import and build the real GET client with nothing outside the tree."""

    def real(self):
        return real_edge()

    def test_the_edge_closure_alone_imports_and_builds_the_get_client(self):
        rel.stage(C, os.path.join(tempfile.mkdtemp(), "tree"), github(self.real()))            # the real isolated import + dry client construction

    def test_a_tree_without_the_notion_transport_or_limits_is_incomplete(self):
        for missing in ("lifeos/platform/limits.py", "lifeos/platform/notion_client.py"):
            d = tempfile.mkdtemp()
            for p, data in self.real().items():
                if p != missing:
                    os.makedirs(os.path.join(d, os.path.dirname(p)), exist_ok=True)
                    open(os.path.join(d, p), "wb").write(data)
            with self.assertRaises(rel.Refused):
                rel.imports(d)


class AutoRelease(unittest.TestCase):
    """Jim, Oct 24: the Edge releases main's head by itself, only once unit-tests passed on it, and only when the Close closure changed."""
    GREEN = {"name": "unit-tests", "status": "completed", "conclusion": "success", "head_sha": C, "app": {"slug": "github-actions"}}

    def fetch(self, sha=C, runs=(GREEN,)):
        return {f"https://api.github.com/repos/{rel.REPO}/commits/main": json.dumps({"sha": sha}).encode(),
                f"https://api.github.com/repos/{rel.REPO}/commits/{sha}/check-runs?check_name=unit-tests": json.dumps({"check_runs": list(runs)}).encode()}.__getitem__

    def test_only_a_green_head_is_released(self):
        self.assertEqual(rel.latest(self.fetch()), C)
        G = self.GREEN
        for runs in ((), ({**G, "conclusion": "failure"},), ({**G, "status": "in_progress", "conclusion": None},), ({**G, "name": "lane-guard"},),
                     ({**G, "head_sha": "b" * 40},), ({**G, "app": {"slug": "some-other-app"}},)):
            with self.assertRaises(rel.Refused):
                rel.latest(self.fetch(runs=runs))
        with self.assertRaises(rel.Refused):
            rel.latest(self.fetch(sha="main"))
        early = {**G, "id": 1}
        rerun = {**G, "id": 2, "conclusion": "failure"}
        with self.assertRaises(rel.Refused):
            rel.latest(self.fetch(runs=(early, {**G, "id": 2, "status": "queued", "conclusion": None, "started_at": None})))   # a queued re-run decides nothing yet
        with self.assertRaises(rel.Refused):
            rel.latest(self.fetch(runs=(early, rerun)))                        # the newest run decides: a failed re-run beats an earlier pass
        self.assertEqual(rel.latest(self.fetch(runs=({**early, "conclusion": "failure"}, {**rerun, "conclusion": "success"}))), C)

    def test_the_import_probe_never_runs_a_commit_as_root(self):
        from unittest import mock
        seen = []
        class Done:
            returncode = 0
        with mock.patch.object(rel.os, "geteuid", return_value=0), mock.patch.object(rel.os.path, "isdir", lambda p: p == "/run/systemd/system"), \
                mock.patch.object(rel.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or Done()):
            rel.imports("/tmp/x")
        self.assertEqual(seen[0][:2], ["systemd-run", "--quiet"])
        self.assertIn("DynamicUser=yes", seen[0])
        self.assertIn("PrivateNetwork=yes", seen[0])
        with mock.patch.object(rel.os, "geteuid", return_value=1000), mock.patch.object(rel.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or Done()):
            rel.imports("/tmp/x")
        self.assertEqual(seen[1][0], rel.sys.executable)

    def test_a_commit_that_does_not_touch_the_closure_is_not_a_release(self):
        a, b = os.path.join(tempfile.mkdtemp(), "a"), os.path.join(tempfile.mkdtemp(), "b")
        rel.stage(C, a, github(), importer=lambda p: None)
        rel.stage(C, b, github(), importer=lambda p: None)
        with open(os.path.join(b, "MANIFEST")) as f:
            body = f.read().splitlines()[1:]
        with open(os.path.join(b, "MANIFEST"), "w") as f:
            f.write("commit " + "b" * 40 + "\n" + "".join(line + "\n" for line in body))
        self.assertTrue(rel.same(a, b))                                        # another commit, the same files
        changed = {**FILES, "lifeos/hiring/close.py": b"# changed\n"}
        c = os.path.join(tempfile.mkdtemp(), "c")
        rel.stage(C, c, github(changed), importer=lambda p: None)
        self.assertFalse(rel.same(a, c))

    def test_the_auto_step_restores_the_live_tree_and_never_updates_the_updater(self):
        script = open(os.path.join(ROOT, "lifeos/edge/oracle/install-close.sh")).read()
        auto = script.split("  auto)", 1)[1].split("  auto-units)", 1)[0]
        self.assertIn('LIFEOS_CLOSE_AUTO=1 "$0" install "$commit"', auto)
        self.assertIn('ln -sfn "$cur"', auto)                                   # back to exactly the tree that was live, never an older "previous"
        self.assertIn('ln -sfn "$prev"', auto)                                  # and its own previous tree is kept
        self.assertNotIn('"$0" rollback', auto)                               # restores exactly, never through rollback's older target
        self.assertEqual(auto.count('> "$ETC/auto.attempts"'), 2)               # a stage or an install counts (before the switch); at most three per commit
        self.assertIn("case \"$n\" in ''|*[!0-9]*|???*) n=0", auto)               # a malformed or huge count is never fatal
        self.assertLess(auto.index("auto.pending\"; then"), auto.index("latest"))  # a cut-off release is settled first, whatever main's head is now
        self.assertIn("RESTORE VERIFY FAILED", auto)
        self.assertIn("the first install is Jim's by hand", auto)
        self.assertIn('[ "$(serve_state)" = ours ]', auto)                      # never re-adds a service Jim uninstalled
        self.assertIn("flock -n 9", auto)
        self.assertIn("trap", auto)
        install = script.split("  install)", 1)[1].split("  auto)", 1)[0]
        self.assertNotIn("export", install)                                     # Jim's own install is never mistaken for an automatic one
        self.assertIn('"${LIFEOS_CLOSE_AUTO:-}" != 1 ] || [ "$st" = ours ]', install)
        self.assertIn('echo "$rolled 3"', script.split("  rollback)", 1)[1])     # a rolled-back commit is never put back
        uninstall = script.split("  uninstall)", 1)[1]
        self.assertIn("disable --now lifeos-close-release.timer", uninstall)
        self.assertIn("flock -w 120 9", uninstall.split(";;", 1)[0])           # waits out a running release
        units = script.split("  auto-units)", 1)[1].split(";;", 1)[0]
        self.assertIn('ExecStart=$OPT/bin/install-close.sh auto', units)        # the timer runs Jim's installed copy, not a file main can change
