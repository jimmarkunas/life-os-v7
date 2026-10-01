"""Web acquisition: status-aware listing, classification, cheap suppression, and one bounded run over an in-memory repo."""
from datetime import date, datetime, timedelta
import json
import unittest

from lifeos.platform import limits
from lifeos.platform.http import Fetched
from lifeos.sources.web import diff, lister, run, suppress

NOW = datetime(2026, 10, 1, 12, 0)


def fetcher(routes):
    """A fake fetch: url fragment -> (status, body)."""
    def fake(url, **kwargs):
        for fragment, (status, body) in routes.items():
            if fragment in url:
                return Fetched(url, status, body if isinstance(body, str) else json.dumps(body))
        return Fetched(url, 0, "", error="network")
    return fake


def gh(*jobs):
    return {"jobs": [{"id": i, "title": t, "absolute_url": f"https://boards.greenhouse.io/x/jobs/{i}", "location": {"name": loc},
                      "first_published": "2026-09-28T10:00:00Z", "content": "&lt;p&gt;" + ("Lead program delivery. " * 20) + "&lt;/p&gt;"}
                     for i, t, loc in jobs]}


class Listing(unittest.TestCase):
    def test_greenhouse_complete_with_description_and_posted_date(self):
        got = lister.list_board("greenhouse", "x", fetcher({"boards-api": (200, gh((1, "Program Manager", "Remote - US")))}))
        self.assertEqual(got.status, lister.COMPLETE)
        job = got.jobs[0]
        self.assertEqual((job["id"], job["posted"], job["location"]), ("1", date(2026, 9, 28), "Remote - US"))
        self.assertIn("Lead program delivery", job["content"])

    def test_an_empty_valid_board_is_complete_zero(self):
        self.assertEqual(lister.list_board("greenhouse", "x", fetcher({"boards-api": (200, {"jobs": []})})).status, lister.COMPLETE)

    def test_failures_are_never_zero(self):
        for status, body, why in ((429, "", "rate_limited"), (403, "", "rate_limited"), (404, "", "not_found"), (500, "", "http_500"),
                                  (200, "{not json", "bad_json"), (200, {"nope": 1}, "bad_shape")):
            got = lister.list_board("greenhouse", "x", fetcher({"boards-api": (status, body)}))
            self.assertEqual((got.status, got.jobs, got.reason), (lister.FAILED, [], why), status)
        self.assertEqual(lister.list_board("greenhouse", "x", fetcher({})).reason, "network")

    def test_ashby_unlisted_jobs_are_skipped_and_smartrecruiters_pages(self):
        ashby = {"jobs": [{"id": "a", "title": "Product Manager", "location": "Remote", "jobUrl": "https://jobs.ashbyhq.com/x/a",
                           "publishedAt": "2026-09-30T00:00:00Z", "descriptionPlain": "d" * 300},
                          {"id": "b", "title": "Hidden", "location": "Remote", "jobUrl": "https://jobs.ashbyhq.com/x/b", "isListed": False}]}
        self.assertEqual([j["id"] for j in lister.list_board("ashby", "x", fetcher({"ashbyhq": (200, ashby)})).jobs], ["a"])
        page = {"content": [{"id": str(i), "name": f"Program Manager {i}", "location": {"city": "Austin", "country": "us"}} for i in range(100)]}
        last = {"content": [{"id": "z", "name": "Product Manager", "location": {"remote": True}}]}
        calls = []

        def paged(url, **kw):
            calls.append(url)
            return Fetched(url, 200, json.dumps(page if "offset=0" in url else last))
        got = lister.list_board("smartrecruiters", "x", paged)
        self.assertEqual((got.status, len(got.jobs), len(calls)), (lister.COMPLETE, 101, 2))
        self.assertIsNone(got.jobs[0]["content"])                      # the list carries no description: enrich reads the page

    def test_rows_without_identity_are_not_jobs(self):
        got = lister.list_board("greenhouse", "x", fetcher({"boards-api": (200, {"jobs": [{"id": 1, "title": "", "absolute_url": "u"}]})}))
        self.assertEqual(got.jobs, [])


class Diffing(unittest.TestCase):
    def job(self, i, title="Program Manager", content="c"):
        return {"id": i, "title": title, "location": "Remote", "url": f"https://x/{i}", "posted": None, "content": content}

    def test_new_changed_unchanged_removed(self):
        a, b, c = self.job("a"), self.job("b"), self.job("c")
        prev = {"a": diff.material_hash(a), "b": diff.material_hash(b), "gone": "h"}
        new, changed, unchanged, removed = diff.classify(prev, [a, self.job("b", content="new text"), c], True)
        self.assertEqual(([j["id"] for j, _ in new], [j["id"] for j, _ in changed], unchanged, removed), (["c"], ["b"], ["a"], ["gone"]))

    def test_a_partial_listing_never_implies_removals(self):
        self.assertEqual(diff.classify({"gone": "h"}, [], False)[3], [])


class Suppression(unittest.TestCase):
    def j(self, title, where="Remote - US", posted=None):
        return {"title": title, "location": where, "posted": posted}

    def test_unequivocal_misses_only(self):
        self.assertEqual(suppress.reason(self.j("Senior Software Engineer")), "off_target_title")
        self.assertEqual(suppress.reason(self.j("Account Executive")), "off_target_title")
        self.assertEqual(suppress.reason(self.j("Program Manager", "London, UK")), "non_us")
        self.assertEqual(suppress.reason(self.j("Program Manager", "Austin, TX (Hybrid)")), "not_remote")
        self.assertEqual(suppress.reason(self.j("Program Manager", posted=date(2026, 8, 1)), NOW.date()), "stale")

    def test_target_family_and_ambiguity_continue(self):
        self.assertIsNone(suppress.reason(self.j("Technical Program Manager, DevOps Platform")))      # not killed by 'DevOps'
        self.assertIsNone(suppress.reason(self.j("Engineering Program Manager")))
        self.assertIsNone(suppress.reason(self.j("Product Manager", "Remote")))                        # unknown market passes
        self.assertIsNone(suppress.reason(self.j("Director, Strategy", "New York, NY")))


class MemRepo:
    def __init__(self):
        self.state, self.items_by, self.commits = {}, {}, []

    def states(self, ids):
        return {k: v for k, v in self.state.items() if k in ids}

    def items(self, sid):
        return dict(self.items_by.get(sid, {}))

    def commit(self, o, failures):
        self.commits.append(o)
        self.state[o.source["id"]] = {"due_at": o.due_at, "failures": 0 if o.status == "COMPLETE" else failures + 1}
        if o.status == "COMPLETE":
            book = self.items_by.setdefault(o.source["id"], {})
            for job, digest, ingest, _, _ in o.items:
                book[job["id"]] = (digest, ingest)
            for i in o.removed:
                book.pop(i, None)


SOURCES = [{"id": "s1", "company": "S1", "kind": "greenhouse", "slug": "s1"}, {"id": "s2", "company": "S2", "kind": "greenhouse", "slug": "s2"}]


def fixed(listings):
    return lambda kind, slug: listings[slug]


class OneRun(unittest.TestCase):
    def listing(self, *rows):
        return lister.list_board("greenhouse", "x", fetcher({"boards-api": (200, gh(*rows))}))

    def test_first_run_classifies_suppresses_and_admits(self):
        repo = MemRepo()
        first = self.listing((1, "Program Manager", "Remote - US"), (2, "Software Engineer", "Remote - US"), (3, "Product Manager", "London, UK"))
        got = run.run(10, True, NOW, fixed({"s1": first, "s2": first}), repo, SOURCES)
        self.assertEqual((got["complete"], got["added"], got["suppressed"], got["admit"]), (2, 6, 4, 2))
        self.assertEqual(got["why"], {"off_target_title": 2, "non_us": 2})

    def test_unchanged_replay_does_no_work_and_a_failed_board_keeps_state(self):
        repo = MemRepo()
        board = self.listing((1, "Program Manager", "Remote - US"))
        run.run(10, True, NOW, fixed({"s1": board, "s2": board}), repo, SOURCES)
        later = NOW + timedelta(hours=limits.WEB_REFRESH_HOURS + 2)
        again = run.run(10, True, later, fixed({"s1": board, "s2": lister.Listing(lister.FAILED, reason="rate_limited")}), repo, SOURCES)
        self.assertEqual((again["unchanged"], again["admit"], again["added"], again["removed"]), (1, 0, 0, 0))
        self.assertEqual((again["failed"], again["failed_why"]), (1, {"rate_limited": 1}))
        self.assertEqual(len(repo.items("s2")), 1)                                  # the failed board's accepted state is untouched
        self.assertGreater(repo.state["s2"]["failures"], 0)

    def test_removal_only_from_a_complete_listing(self):
        repo = MemRepo()
        run.run(10, True, NOW, fixed({"s1": self.listing((1, "Program Manager", "Remote - US"), (2, "Product Manager", "Remote - US")),
                                      "s2": self.listing()}), repo, SOURCES)
        later = NOW + timedelta(hours=8)
        got = run.run(10, True, later, fixed({"s1": self.listing((1, "Program Manager", "Remote - US")), "s2": self.listing()}), repo, SOURCES)
        self.assertEqual(got["removed"], 1)

    def test_only_due_boards_run_and_dry_run_writes_nothing(self):
        repo = MemRepo()
        board = self.listing((1, "Program Manager", "Remote - US"))
        dry = run.run(10, False, NOW, fixed({"s1": board, "s2": board}), repo, SOURCES)
        self.assertEqual((dry["due"], dry["saved"], repo.commits), (2, False, []))
        run.run(10, True, NOW, fixed({"s1": board, "s2": board}), repo, SOURCES)
        self.assertEqual(run.run(10, True, NOW + timedelta(minutes=5), fixed({}), repo, SOURCES)["due"], 0)

    def test_the_ingest_budget_leaves_a_pending_backlog_that_drains(self):
        repo = MemRepo()
        rows = [(i, "Program Manager", "Remote - US") for i in range(5)]
        board = self.listing(*rows)
        old = limits.WEB_INGEST_PER_RUN
        limits.WEB_INGEST_PER_RUN = 3
        try:
            first = run.run(10, True, NOW, fixed({"s1": board, "s2": self.listing()}), repo, SOURCES)
            self.assertEqual((first["admit"], first["pending"]), (3, 2))
            second = run.run(10, True, NOW + timedelta(minutes=1), fixed({"s1": board, "s2": self.listing()}), repo, SOURCES)
            self.assertEqual((second["admit"], second["pending"]), (2, 0))             # a backlog board is due again at once
        finally:
            limits.WEB_INGEST_PER_RUN = old


if __name__ == "__main__":
    unittest.main()
