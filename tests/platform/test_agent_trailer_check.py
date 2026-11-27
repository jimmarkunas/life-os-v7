import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import agent_trailer_check as check  # noqa: E402

OK = "[LIFE-1] x\n\nCo-Authored-By: Someone\nAgent: MC01"
EVENT = {"pull_request": {"number": 7, "body": "text\n\nAgent: OC"}}


def go(body, commits, name="pull_request", raises=False):
    event = {"pull_request": {"number": 7, "body": body}}

    def fetch(*_):
        if raises:
            raise OSError("down")
        return commits

    out = io.StringIO()
    return check.run(name, event, "o/r", "", fetch=fetch, out=out), out.getvalue()


class Trailer(unittest.TestCase):
    def test_every_commit_and_the_description_with_a_trailer_passes(self):
        code, text = go("a\nAgent: MC01", [("a" * 40, OK), ("b" * 40, "x\n\nAgent: Codex")])
        self.assertEqual((code, "ok (2 commits" in text), (0, True))

    def test_a_commit_without_the_trailer_fails_and_names_its_short_id(self):
        code, text = go("a\nAgent: MC01", [("a" * 40, OK), ("b" * 40, "no trailer here")])
        self.assertEqual(code, 1)
        self.assertIn("1 commit(s)", text)
        self.assertIn("bbbbbbb", text)

    def test_a_description_without_the_trailer_fails(self):
        code, text = go("nothing", [("a" * 40, OK)])
        self.assertEqual(code, 1)
        self.assertIn("description has no", text)

    def test_the_trailer_must_be_a_whole_line_with_a_known_name(self):
        for bad in ("Agent: Someone", "Agent: MC01 and more", "the Agent: OC said", "agent: oc"):
            self.assertEqual(go("x\n" + bad, [])[0], 1, bad)
        self.assertEqual(go("x\nAgent: Jim", [])[0], 0)

    def test_a_trailer_among_other_trailers_counts(self):
        message = "x\n\nCo-Authored-By: A\nAgent: OC\nSigned-off-by: B"
        self.assertEqual(go("Agent: OC", [("c" * 40, message)])[0], 0)

    def test_only_a_pull_request_event_is_checked(self):
        self.assertEqual(go("none", [("a" * 40, "none")], name="merge_group")[0], 0)
        self.assertEqual(go("none", [("a" * 40, "none")], name="push")[0], 0)

    def test_an_unreadable_commit_list_warns_and_still_checks_the_description(self):
        code, text = go("a\nAgent: MC01", [], raises=True)
        self.assertEqual((code, "::warning::" in text), (0, True))
        self.assertEqual(go("none", [], raises=True)[0], 1)

    def test_commits_are_read_a_page_at_a_time(self):
        pages = [
            [{"sha": "a" * 40, "commit": {"message": "m%d" % i}} for i in range(100)],
            [{"sha": "b" * 40, "commit": {"message": "last"}}],
        ]

        class Response:
            def __init__(self, rows):
                self.rows = rows

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                import json  # noqa: PLC0415

                return json.dumps(self.rows).encode()

        seen = []

        def opener(request, timeout=0):
            seen.append(request.full_url)
            return Response(pages[len(seen) - 1])

        self.assertEqual(len(check.fetch_commits("o/r", 7, "", opener=opener)), 101)
        self.assertEqual(len(seen), 2)


if __name__ == "__main__":
    unittest.main()
