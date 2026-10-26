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
    """Jim, Oct 24: the Edge releases main's head by itself, only once main's own hourly run tested that commit, and only when the Close closure changed."""
    RUN = {"head_sha": C, "head_branch": "main", "path": ".github/workflows/hourly.yml", "status": "completed", "id": 1, "run_attempt": 1}

    def fetch(self, sha=C, runs=(RUN,), steps=None):
        steps = steps or {1: "success"}
        urls = {f"https://api.github.com/repos/{rel.REPO}/commits/main": json.dumps({"sha": sha}).encode(),
                f"https://api.github.com/repos/{rel.REPO}/actions/workflows/hourly.yml/runs?branch=main&head_sha={sha}&per_page=20": json.dumps({"workflow_runs": list(runs)}).encode()}
        for run_id, conclusion in steps.items():
            urls[f"https://api.github.com/repos/{rel.REPO}/actions/runs/{run_id}/jobs?per_page=100"] = json.dumps(
                {"jobs": [{"name": "prep", "steps": [{"name": "Unit tests", "conclusion": conclusion}]}]}).encode()
        return urls.__getitem__

    def test_only_main_own_tested_head_releases(self):
        R = self.RUN
        self.assertEqual(rel.latest(self.fetch()), C)
        refused = [dict(runs=()), dict(steps={1: "failure"}), dict(steps={1: "skipped"}), dict(runs=({**R, "status": "in_progress"},)),
                   dict(runs=({**R, "head_branch": "feature"},)), dict(runs=({**R, "head_sha": "b" * 40},)), dict(runs=({**R, "path": ".github/workflows/other.yml"},)),
                   dict(runs=(R, {**R, "id": 2}), steps={1: "success", 2: "failure"}), dict(runs=(R, {**R, "id": 2, "status": "queued"}))]
        for kw in refused:
            with self.assertRaises(rel.Refused, msg=str(kw)):
                rel.latest(self.fetch(**kw))
        self.assertEqual(rel.latest(self.fetch(runs=(R, {**R, "id": 2}), steps={1: "success", 2: "skipped"})), C)    # a skipped tick tests nothing: the older test stands
        with self.assertRaises(rel.Refused):
            rel.latest(self.fetch(sha="main"))

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

    def test_the_install_script_knows_the_settings_credential_and_loads_it_into_the_unit(self):
        script = open(os.path.join(ROOT, "lifeos/edge/oracle/install-close.sh")).read()
        for text in ("ghvars)", "ghvars-off)", "LoadCredentialEncrypted=ghvars:$CREDS/lifeos-close.ghvars", "Variables read and write, nothing else"):
            self.assertIn(text, script)

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
        self.assertIn('remember "$OPT/current"', script.split("  rollback)", 1)[1])   # rolled-back code was live: never brought back
        self.assertIn('"$0" verify && remember "$dest"', install)               # every install (auto or Jim's) remembers what went live
        self.assertIn('if seen "$probe"; then', auto)
        uninstall = script.split("  uninstall)", 1)[1]
        self.assertIn("disable --now lifeos-close-release.timer", uninstall)
        self.assertIn("flock -w 120 9", uninstall.split(";;", 1)[0])           # waits out a running release
        units = script.split("  auto-units)", 1)[1].split(";;", 1)[0]
        self.assertIn('ExecStart=$OPT/bin/install-close.sh auto', units)        # the timer runs Jim's installed copy, not a file main can change
