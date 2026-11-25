"""LIFE-981: the one-time copy: names from the workflows, values only in memory, readback by hash, fixed codes, nothing printed that is a value. Synthetic data only; no network."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lifeos.platform import doppler_copy as c

ID1, ID2, ID3, IDT = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333", "99999999-9999-4999-8999-999999999999")


def tree(lines):
    root = tempfile.mkdtemp()
    (Path(root) / ".github" / "workflows").mkdir(parents=True)
    (Path(root) / ".github" / "workflows" / "a.yml").write_text("\n".join(lines))
    return root


class FakeDoppler:
    def __init__(self, held=None, fail_write=False):
        self.held, self.fail_write, self.posts = dict(held or {}), fail_write, 0

    def __call__(self, method, url, token, body=None, opener=None):
        assert token == "WRITE-TOKEN-VALUE-1234"
        if method == "GET":
            return dict(self.held)
        self.posts += 1
        if self.fail_write:
            raise OSError("down")
        self.held.update(body["secrets"])
        return {"secrets": {k: {"raw": v} for k, v in body["secrets"].items()}}


def bws(items):
    return lambda argv, **kw: SimpleNamespace(returncode=0, stdout=json.dumps([{"id": i, "key": k, "value": v} for i, (k, v) in items.items()]))


ITEMS = {ID1: ("lifeos/github-actions/ALPHA", "alpha-secret-value-1"), ID2: ("lifeos/github-actions/BETA", "beta-secret-value-22"), IDT: ("lifeos/github-actions/DOPPLER_PARITY_WRITE_TOKEN", "WRITE-TOKEN-VALUE-1234")}
LINES = [f"            {ID1} > ALPHA", f"            {ID2} > BETA   # note", "  other: line"]


class Copy(unittest.TestCase):
    def go(self, doppler, items=ITEMS, lines=LINES):
        out = []
        code = c.main(tree(lines), bws(items), {}, doppler, out.append)
        return code, out

    def test_pairs_come_from_the_workflows_and_conflicts_are_dropped(self):
        found, conflicts = c.pairs(tree(LINES + [f"            {ID3} > ALPHA"]))
        self.assertEqual((found, conflicts), ({"BETA": ID2}, ["ALPHA"]))

    def test_everything_is_copied_then_read_back_and_no_value_is_printed(self):
        d = FakeDoppler()
        code, out = self.go(d)
        self.assertEqual(code, 0)
        result = json.loads(out[-1].split("doppler-copy: ", 1)[1])
        self.assertEqual((result["copied"], result["already_equal"], result["mismatched"], result["missing_after"], result["failed"]), (2, 0, 0, 0, 0))
        self.assertEqual(set(d.held), {"ALPHA", "BETA"})
        text = "\n".join(l for l in out if not l.startswith("::add-mask::"))
        for secret in ("alpha-secret-value-1", "beta-secret-value-22", "WRITE-TOKEN-VALUE-1234"):
            self.assertNotIn(secret, text)

    def test_a_second_run_writes_nothing_and_a_wrong_value_is_overwritten(self):
        d = FakeDoppler({"ALPHA": "alpha-secret-value-1", "BETA": "stale"})
        code, out = self.go(d)
        result = json.loads(out[-1].split("doppler-copy: ", 1)[1])
        self.assertEqual((code, result["already_equal"], result["copied"], d.held["BETA"]), (0, 1, 1, "beta-secret-value-22"))
        self.assertEqual(self.go(d)[0], 0)
        self.assertEqual(d.posts, 1)

    def test_fixed_codes_for_the_failures(self):
        self.assertIn("write_token_missing", self.go(FakeDoppler(), {ID1: ITEMS[ID1]})[1][-1])
        out = []
        self.assertEqual(c.main(tree(LINES), lambda *a, **k: SimpleNamespace(returncode=1, stdout=""), {}, FakeDoppler(), out.append), 1)
        self.assertIn("bitwarden_unreadable", out[-1])
        code, out = self.go(FakeDoppler(fail_write=True))
        self.assertEqual(code, 1)
        self.assertIn("doppler_write_failed", out[-1])
        code, out = self.go(FakeDoppler(), {**ITEMS, ID2: ("lifeos/github-actions/BETA", "")})
        self.assertEqual(code, 1)
        self.assertIn("empty_in_bitwarden", out[-1])

    def test_the_workflow_is_manual_only(self):
        text = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "doppler-copy.yml").read_text()
        self.assertIn("workflow_dispatch:", text)
        for trigger in ("schedule", "push:", "pull_request", "workflow_run"):
            self.assertNotIn(trigger, text.split("jobs:")[0].split("\n", 6)[-1])
