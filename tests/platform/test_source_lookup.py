"""LIFE-888 (synthetic fixtures): canonical source lookup. No network, no Notion, no GitHub."""
import json
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone

from lifeos.platform import agent_directory as D
from lifeos.platform import source_lookup as S
from tests.platform import agent_directory_fixture as F

NOW = datetime(2026, 11, 22, 20, 0, tzinfo=timezone.utc)
SHA, LAST, BLOB = "a" * 40, "b" * 40, "c" * 40
PAGE = "[removed]"


def gh(modified=NOW - timedelta(days=2), text="# Policy\nrules"):
    return lambda path, ref: {"commit": SHA, "last_change": LAST, "blob": BLOB, "modified": modified, "text": text}


def nt(modified=NOW - timedelta(hours=3), text="spec body"):
    return lambda page: {"id": page, "title": "Spec", "modified": modified, "text": text}


def refused(args, fetchers):
    with unittest.TestCase().assertRaises(S.LookupError_) as raised:
        S.read_source(args, fetchers, NOW)
    return raised.exception.reason


class GitHubAndNotion(unittest.TestCase):
    def test_a_github_file_returns_its_commit_sha_revision_ct_read_time_and_freshness(self):
        out = S.read_source({"source": "github", "path": "docs/life-os-development-policy.md"}, {"github": gh()}, NOW)
        self.assertEqual((out["revision"]["commit_sha"], out["revision"]["blob_sha"], out["freshness"], out["ref"], out["repo"]), (SHA, BLOB, "fresh", "main", S.REPO))
        self.assertRegex(out["read_at"], r"CT$")
        self.assertEqual(out["age_seconds"], 2 * 86400)

    def test_a_notion_page_returns_last_edited_time_and_says_the_api_has_no_page_version(self):
        out = S.read_source({"source": "notion", "page_id": PAGE.upper()}, {"notion": nt()}, NOW)
        self.assertEqual((out["revision"]["last_edited_time"], out["revision"]["page_version"], out["freshness"]), ((NOW - timedelta(hours=3)).isoformat(), None, "fresh"))
        self.assertIn("no page version", out["revision"]["page_version_note"])

    def test_a_source_older_than_the_callers_limit_is_stale_source(self):
        self.assertEqual(refused({"source": "github", "path": "AGENTS.md", "max_age_seconds": 60}, {"github": gh()}), "stale_source")
        self.assertEqual(refused({"source": "notion", "page_id": PAGE, "max_age_seconds": 60}, {"notion": nt()}), "stale_source")
        self.assertEqual(refused({"source": "github", "path": "AGENTS.md"}, {"github": gh(modified=NOW - timedelta(days=91))}), "stale_source")     # the default limit

    def test_unknown_or_malformed_sources_are_unknown_source_and_nothing_is_fetched(self):
        called = []
        fetchers = {"github": lambda *a: called.append(a), "notion": lambda *a: called.append(a)}
        for args in ({"source": "jira"}, {"source": "github", "path": "../etc/passwd"}, {"source": "github", "path": "/abs"}, {"source": "github"}, {"source": "github", "path": "a", "ref": "x..y"},
                     {"source": "notion", "page_id": "not-an-id"}, {"source": "notion"}, {}):
            self.assertEqual(refused(args, fetchers), "unknown_source", args)
        self.assertEqual(called, [])

    def test_an_unwired_or_failing_fetcher_is_source_unavailable_and_a_bad_limit_is_bad_request(self):
        self.assertEqual(refused({"source": "notion", "page_id": PAGE}, {}), "source_unavailable")
        self.assertEqual(refused({"source": "github", "path": "AGENTS.md"}, {"github": lambda p, r: 1 / 0}), "source_unavailable")
        self.assertEqual(refused({"source": "github", "path": "AGENTS.md", "max_age_seconds": 0}, {"github": gh()}), "bad_request")
        self.assertEqual(refused({"source": "github", "path": "AGENTS.md", "max_age_seconds": True}, {"github": gh()}), "bad_request")

    def test_a_secret_in_the_text_is_refused_and_long_text_is_cut(self):
        self.assertEqual(refused({"source": "github", "path": "a.md"}, {"github": gh(text="api_key = sk-abcdefghijklmnopqrstuvwxyz")}), "output_has_secret")
        out = S.read_source({"source": "github", "path": "a.md"}, {"github": gh(text="x" * 9000)}, NOW)
        self.assertEqual((len(out["content"]), out["truncated"]), (S.MAX_TEXT, True))


class LiveGitHubReader(unittest.TestCase):
    def test_the_reader_resolves_the_ref_to_a_commit_then_reads_the_file_at_that_commit(self):
        import base64
        seen = []

        def opener(request, timeout):
            seen.append(request.full_url)

            class Reply:
                def __init__(self, body):
                    self.body = body

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return json.dumps(self.body).encode()
            url = request.full_url
            if "/commits/main" in url:
                return Reply({"sha": SHA})
            if "/commits?" in url:
                return Reply([{"sha": LAST, "commit": {"committer": {"date": "2026-11-20T12:00:00Z"}}}])
            return Reply({"type": "file", "encoding": "base64", "sha": BLOB, "content": base64.b64encode(b"hello").decode()})
        got = S.github_fetch(opener)("docs/x.md", "main")
        self.assertEqual((got["commit"], got["last_change"], got["blob"], got["text"]), (SHA, LAST, BLOB, "hello"))
        self.assertTrue(seen[2].endswith(f"?ref={SHA}"))                                   # the file is read at the commit the ref named, so SHA and text agree
        self.assertTrue(all(u.startswith(f"https://api.github.com/repos/{S.REPO}/") for u in seen))

    def test_a_missing_path_is_unknown_source_and_a_server_error_is_source_unavailable(self):
        def failing(code):
            def opener(request, timeout):
                raise urllib.error.HTTPError(request.full_url, code, "x", {}, None)
            return opener
        for code, reason in ((404, "unknown_source"), (500, "source_unavailable")):
            with self.assertRaises(S.LookupError_) as raised:
                S.github_fetch(failing(code))("a.md", "main")
            self.assertEqual(raised.exception.reason, reason)


class AgentLookup(unittest.TestCase):
    NOW = datetime(2026, 11, 16, 12, 0, tzinfo=D.CENTRAL)

    def look(self, **args):
        return S.lookup_agent(args, lambda: F.rows(), self.NOW)

    def test_a_lookup_shows_the_directory_status_class_and_never_upgrades_it(self):
        out = self.look(agent_id="bot_hermes_claude-hermes")
        self.assertEqual((out["agent"]["agent_id"], out["status"], out["authorizes"]), ("bot_hermes_claude-hermes", "configured-untested", []))
        self.assertNotEqual(out["status"], "routable-verified")                           # no proof is ever supplied here
        for row in F.rows():
            try:
                self.assertNotEqual(self.look(agent_id=row["Agent ID"])["status"], "routable-verified", row["Agent ID"])
            except S.LookupError_:
                pass

    def test_refusals_carry_the_directorys_fixed_reasons(self):
        for args, reason in (({"agent_id": "nobody_here"}, "unknown_agent"), ({"alias": "*"}, "broadcast_refused"), ({}, "bad_request"), ({"agent_id": "a", "alias": "b"}, "bad_request")):
            with self.assertRaises(S.LookupError_) as raised:
                self.look(**args)
            self.assertEqual(raised.exception.reason, reason, args)

    def test_an_unwired_directory_is_directory_unavailable_and_a_failing_one_too(self):
        for fetch in (None, lambda: 1 / 0):
            with self.assertRaises(S.LookupError_) as raised:
                S.lookup_agent({"agent_id": "bot_hermes_claude-hermes"}, fetch, self.NOW)
            self.assertEqual(raised.exception.reason, "directory_unavailable")


if __name__ == "__main__":
    unittest.main()
