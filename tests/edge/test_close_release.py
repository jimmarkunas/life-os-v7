"""LIFE-47 S2 release verification and fail-closed Serve reading. A fake GitHub (URL -> bytes) only; no network."""
import json
import os
import tempfile
import unittest

from lifeos.edge.oracle import close_release as rel

C = "a" * 40
APP_SRC = b'CLOSURE = ("lifeos/hiring/close.py",)\n'
FILES = {"lifeos/__init__.py": b"", "lifeos/edge/close_gate.py": b"# gate\n", "lifeos/imessage/__init__.py": b"", "lifeos/imessage/alerts_endpoint.py": b"# ep\n",
         "lifeos/hiring/__init__.py": b"", rel.APP: APP_SRC, "lifeos/hiring/close.py": b"# close\n"}


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
        rel.stage(C, self.dest(), github())                              # the real import check passes when the declared closure is complete
        leaky = {**FILES, rel.APP: b'import lifeos.hiring.not_declared\nCLOSURE = ("lifeos/hiring/close.py",)\n'}
        with self.assertRaises(rel.Refused) as caught:
            rel.stage(C, self.dest(), github(leaky))                     # close_app imports a module its CLOSURE does not ship
        self.assertEqual(str(caught.exception), "CLOSURE_INCOMPLETE")

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
