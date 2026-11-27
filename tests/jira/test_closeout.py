"""(synthetic, no network): one call stamps, comments, moves to Done and reads both back"""

import io
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.jira import closeout

SHA = "a" * 40
NOW = datetime(2026, 11, 16, 15, 4, tzinfo=ZoneInfo("America/Chicago"))


def doc(text):
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


class FakeJira:
    def __init__(self, status="In Progress", comments=(), sticky=False):
        self.status, self.comments, self.sticky, self.writes = status, list(comments), sticky, []

    def get(self, path, params=None):
        if path.endswith("/comment"):
            return {"comments": [{"body": doc(c)} for c in reversed(self.comments)]}
        return {"fields": {"status": {"name": self.status}}}

    def post(self, path, payload, retry=False):
        self.writes.append(path.rsplit("/", 1)[-1])
        if path.endswith("/comment"):
            self.comments.append("\n".join(p["content"][0]["text"] for p in payload["body"]["content"]))
        elif not self.sticky:
            self.status = "Done"
        return {}


def run(jira, *extra, keys=("LIFE-1",)):
    out = io.StringIO()
    code = closeout.main(
        [*keys, "--pr", "9", "--sha", SHA, "--run", "55", "--proof", "LIFE-1=ran the suite", *extra],
        jira=jira,
        now=NOW,
        out=out,
    )
    return code, out.getvalue()


class Closeout(unittest.TestCase):
    def test_the_receipt_has_a_central_stamp_the_pr_the_sha_the_run_and_every_ticket(self):
        text = closeout.receipt(["LIFE-1", "LIFE-2"], 9, SHA, 55, {"LIFE-1": "ran the suite"}, NOW)
        self.assertIn("Nov 16 2026, 3:04 PM CT", text)
        self.assertIn("pull/9 at aaaaaaaa", text)
        self.assertIn("actions/runs/55", text)
        self.assertIn("LIFE-1: ran the suite", text)
        self.assertIn("LIFE-2: local proof as in the PR", text)
        self.assertNotIn("UTC", text)

    def test_a_dry_run_reads_and_writes_nothing(self):
        jira = FakeJira()
        code, out = run(jira)
        self.assertEqual((code, jira.writes), (0, []))
        self.assertIn("LIFE-1 dry_run", out)

    def test_live_comments_moves_to_done_and_reads_both_back(self):
        jira = FakeJira()
        code, out = run(jira, "--live")
        self.assertEqual((code, jira.writes), (0, ["comment", "transitions"]))
        self.assertIn("LIFE-1 closed", out)
        self.assertEqual(jira.status, "Done")

    def test_a_second_run_writes_nothing(self):
        jira = FakeJira()
        run(jira, "--live")
        before = list(jira.writes)
        code, out = run(jira, "--live")
        self.assertEqual((code, jira.writes), (0, before))
        self.assertIn("already_done", out)

    def test_a_receipt_already_posted_is_not_posted_again(self):
        jira = FakeJira(comments=[closeout.receipt(["LIFE-1"], 9, SHA, 55, {}, NOW)])
        run(jira, "--live")
        self.assertEqual(jira.writes, ["transitions"])

    def test_a_transition_that_does_not_stick_is_partial_and_exits_nonzero(self):
        jira = FakeJira(sticky=True)
        code, out = run(jira, "--live")
        self.assertEqual(code, 1)
        self.assertIn("partial: status reads In Progress", out)

    def test_a_batch_closes_every_ticket_with_the_same_receipt(self):
        one, two = FakeJira(), FakeJira()
        jiras = {"LIFE-1": one, "LIFE-2": two}

        class Router:
            def get(self, path, params=None):
                return jiras[path.split("/")[5]].get(path, params)

            def post(self, path, payload, retry=False):
                return jiras[path.split("/")[5]].post(path, payload, retry)

        code, out = run(Router(), "--live", keys=("LIFE-1", "LIFE-2"))
        self.assertEqual(code, 0)
        self.assertEqual(one.comments, two.comments)
        self.assertIn("LIFE-2", one.comments[0])

    def test_bad_keys_and_shas_are_refused_before_any_call(self):
        out = io.StringIO()
        self.assertEqual(closeout.main(["nope", "--pr", "1", "--sha", "x", "--run", "1"], jira=object(), out=out), 2)


if __name__ == "__main__":
    unittest.main()
