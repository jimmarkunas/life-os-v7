"""Web acquisition: status-aware listing, classification, cheap suppression, and one bounded run over an in-memory repo."""
from datetime import date, datetime, timedelta
import json
import unittest

from unittest import mock

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


class WorkdayJibePinpoint(unittest.TestCase):
    def test_workday_pages_until_total_and_reads_relative_dates(self):
        pages = {0: {"total": 3, "jobPostings": [{"title": "Program Manager", "externalPath": "/job/a_1", "locationsText": "Remote, US", "postedOn": "Posted Today"},
                                                   {"title": "Product Manager", "externalPath": "/job/b_2", "locationsText": "Remote", "postedOn": "Posted 3 Days Ago"}]},
                 20: {"total": 0, "jobPostings": [{"title": "TPM", "externalPath": "/job/c_3", "locationsText": "Remote", "postedOn": "Posted 30+ Days Ago"}]}}
        seen = []

        def fake(url, **kw):
            body = json.loads(kw["data"])
            seen.append((url, body["offset"], body["limit"]))
            return Fetched(url, 200, json.dumps(pages[body["offset"]]))
        # two jobs on the first page are fewer than the page size, so the second request is made only while offset < total
        pages[0]["jobPostings"].append({"title": "x", "externalPath": "/job/z_0", "locationsText": "", "postedOn": ""})
        src = {"kind": "workday", "url": "https://adobe.wd5.myworkdayjobs.com/wday/cxs/adobe/external/jobs",
               "public_job_base_url": "https://adobe.wd5.myworkdayjobs.com/en-US/external"}
        got = lister.list_source(src, fake)
        self.assertEqual((got.status, len(got.jobs), len(seen)), (lister.COMPLETE, 3, 1))
        self.assertEqual(got.jobs[0]["url"], "https://adobe.wd5.myworkdayjobs.com/en-US/external/job/a_1")
        self.assertEqual(got.jobs[0]["posted"], date.today())
        self.assertIsNone(got.jobs[2]["posted"])

    def test_workday_incomplete_and_oversize_are_failed(self):
        src = {"kind": "workday", "url": "https://acme.wd1.myworkdayjobs.com/en-US/careers"}
        self.assertEqual(lister.workday_urls(src)[0], "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/careers/jobs")
        short = lambda url, **kw: Fetched(url, 200, json.dumps({"total": 50, "jobPostings": []}))
        self.assertEqual(lister.list_source(src, short).reason, "incomplete")
        big = lambda url, **kw: Fetched(url, 200, json.dumps({"total": 99999, "jobPostings": []}))
        self.assertEqual(lister.list_source(src, big).reason, "too_many")

    def test_jibe_paginates_and_rejects_a_moving_total(self):
        def page(n, total):
            return {"totalCount": total, "jobs": [{"data": {"slug": f"{n}-{i}", "title": "Program Manager", "full_location": "Remote",
                                                            "description": "<p>" + "Lead delivery. " * 30 + "</p>", "posted_date": "2026-09-30"}} for i in range(2)]}
        src = {"kind": "jibe", "url": "https://x.example/api/jobs", "job_base_url": "https://x.example/jobs"}
        ok = lambda url, **kw: Fetched(url, 200, json.dumps(page(1, 2) if "page=1" in url else {"totalCount": 2, "jobs": []}))
        got = lister.list_source(src, ok)
        self.assertEqual((got.status, len(got.jobs), got.jobs[0]["posted"]), (lister.COMPLETE, 2, date(2026, 9, 30)))
        self.assertIn("Lead delivery", got.jobs[0]["content"])
        moving = lambda url, **kw: Fetched(url, 200, json.dumps(page(1, 4) if "page=1" in url else page(2, 5)))
        self.assertEqual(lister.list_source(src, moving).reason, "inconsistent")

    def test_pinpoint_custom_domain(self):
        data = {"data": [{"id": 7, "title": "Programme Manager", "url": "https://careers.x.example/en/postings/7", "location": {"name": "London"},
                          "description": "<p>" + "Run programmes. " * 30 + "</p>"}]}
        got = lister.list_source({"kind": "pinpoint", "url": "https://careers.x.example/postings.json"},
                                 lambda url, **kw: Fetched(url, 200, json.dumps(data)))
        self.assertEqual((got.status, got.jobs[0]["location"]), (lister.COMPLETE, "London"))


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


class ScaleUp(unittest.TestCase):
    def test_the_universe_is_harvested_and_route_is_the_curated_membership(self):
        from lifeos.sources.web import registry
        rows = registry.load(registry.PATHS["Scale-Up"])
        self.assertEqual(len(rows), 48)
        ready = registry.for_lane("Scale-Up")
        self.assertEqual(sorted({r["kind"] for r in ready if "_html" not in r["kind"] and r["kind"] != "doubleword_bundle"}),
                         ["ashby", "greenhouse", "lever", "pinpoint", "teamtailor", "workable", "workday"])
        self.assertEqual(len(ready), 37)                                          # 19 public ATS boards + 18 first-party careers pages (Futuristic via the announced-bot header; Floww via the WTTJ company page, D111); Otto Car and Truvi are fallback (D51)
        self.assertEqual(sum(r["status"] == "fallback" for r in rows), 11)        # no discoverable ATS: stays DEGRADED, never zero

    def test_scale_up_suppression_is_not_us_remote_suppression(self):
        j = lambda t, where, posted=None: {"title": t, "location": where, "posted": posted}
        self.assertIsNone(suppress.reason(j("Program Manager", "London, UK (Hybrid)"), NOW.date(), "Scale-Up"))        # any work mode
        self.assertIsNone(suppress.reason(j("Program Manager", "London", date(2026, 9, 10)), NOW.date(), "Scale-Up"))  # 21 days: inside 30
        self.assertEqual(suppress.reason(j("Program Manager", "London", date(2026, 8, 20)), NOW.date(), "Scale-Up"), "stale")
        self.assertEqual(suppress.reason(j("Program Manager", "Paris, France"), NOW.date(), "Scale-Up"), "non_target_geography")
        self.assertEqual(suppress.reason(j("Software Engineer", "London"), NOW.date(), "Scale-Up"), "off_target_title")
        self.assertEqual(suppress.reason(j("Program Manager", "Paris, France"), NOW.date(), "US Remote"), "non_us")

    def test_a_scale_up_run_admits_into_the_scale_up_lane_with_route_evidence(self):
        repo = MemRepo()
        src = [{"id": "su-x", "company": "X Ltd", "kind": "greenhouse", "slug": "x", "tier": "employer", "status": "ready"}]
        board = lister.list_board("greenhouse", "x", fetcher({"boards-api": (200, gh((1, "Program Manager", "London, UK"), (2, "Product Manager", "Paris")))}))
        got = run.run(10, True, NOW, lambda source: board, repo, src, lane="Scale-Up")
        self.assertEqual((got["lane"], got["admit"], got["why"]), ("Scale-Up", 1, {"non_target_geography": 1}))
        self.assertEqual(repo.commits[0][1], "Scale-Up")
        self.assertEqual(repo.commits[0][0].source["route_evidence"], "Scale-up:POSITIVE")


class MemRepo:
    def __init__(self):
        self.state, self.items_by, self.commits = {}, {}, []

    def states(self, ids):
        return {k: v for k, v in self.state.items() if k in ids}

    def items(self, sid):
        return dict(self.items_by.get(sid, {}))

    def commit(self, o, failures, lane="US Remote"):
        self.commits.append((o, lane))
        self.state[o.source["id"]] = {"due_at": o.due_at, "failures": 0 if o.status == "COMPLETE" else failures + 1}
        if o.status == "COMPLETE":
            book = self.items_by.setdefault(o.source["id"], {})
            for job, digest, ingest, _, _ in o.items:
                book[job["id"]] = (digest, ingest)
            for i in o.removed:
                book.pop(i, None)


SOURCES = [{"id": "s1", "company": "S1", "kind": "greenhouse", "slug": "s1"}, {"id": "s2", "company": "S2", "kind": "greenhouse", "slug": "s2"}]


def fixed(listings):
    return lambda source: listings[source["slug"]]


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


RSS = """<?xml version="1.0"?><rss xmlns:tt="https://teamtailor.com/locations"><channel><title>Jobs</title>
<item><guid>101</guid><title>Programme Manager</title><link>https://careers.x.com/jobs/101-pm</link><pubDate>Tue, 29 Sep 2026 10:00:00 +0000</pubDate>
<description>&lt;p&gt;Lead delivery&lt;/p&gt;</description><tt:locations><tt:location><tt:city>London</tt:city><tt:country>United Kingdom</tt:country></tt:location></tt:locations></item>
</channel></rss>"""


class Teamtailor(unittest.TestCase):
    def read(self, status, body):
        from lifeos.platform.http import Fetched
        from lifeos.sources.web import lister
        return lister.list_source({"kind": "teamtailor", "url": "https://careers.x.com/jobs"}, lambda url, **kw: Fetched(url, status, body))

    def test_rss_listing(self):
        got = self.read(200, RSS)
        self.assertEqual((got.status, len(got.jobs)), ("COMPLETE", 1))
        job = got.jobs[0]
        self.assertEqual((job["title"], job["location"], str(job["posted"])), ("Programme Manager", "London, United Kingdom", "2026-09-29"))
        self.assertIn("Lead delivery", job["content"])

    def test_empty_board_is_complete_but_a_blocked_or_odd_feed_is_not(self):
        self.assertEqual((self.read(200, '<rss><channel><title>x</title></channel></rss>').status, ), ("COMPLETE",))
        self.assertEqual(self.read(403, "").reason, "rate_limited")
        self.assertEqual(self.read(200, "<html>").reason, "bad_xml")
        self.assertEqual(self.read(200, "<rss></rss>").reason, "bad_shape")
        self.assertEqual(self.read(200, '<!DOCTYPE x [<!ENTITY a "b">]><rss><channel/></rss>').reason, "bad_shape")


class LongLocations(unittest.TestCase):
    """D104: a stored place cut at 200 characters hid London from Revolut jobs; unchanged jobs whose listed place is longer are put right."""

    def test_unchanged_jobs_with_a_long_place_are_relocated(self):
        offices = " | ".join(f"Office {n} · office · Country {n}" for n in range(12)) + " | London · office · United Kingdom"
        job = {"id": "p1", "title": "Product Owner (Technical)", "location": offices, "url": "https://x/p1", "posted": None, "content": None}
        short = {"id": "p2", "title": "Program Manager", "location": "London", "url": "https://x/p2", "posted": None, "content": None}
        listing = lister.Listing(lister.COMPLETE, [job, short])
        previous = {j["id"]: (diff.material_hash(j), "INGESTED") for j in listing.jobs}
        outcome = run.plan({"id": "s", "kind": "revolut_html"}, listing, previous, datetime(2026, 10, 4), 10)
        self.assertEqual(list(outcome.relocate), ["p1"])
        self.assertIn("London", outcome.relocate["p1"])
        self.assertGreater(len(offices), 200)


class ForcedBoard(unittest.TestCase):
    def test_a_named_company_is_due_now_whatever_its_state(self):
        sources = [{"id": "su-revolut-ltd", "company": "Revolut Ltd"}, {"id": "su-plentific", "company": "Plentific"}]
        states = {"su-revolut-ltd": {"due_at": datetime(2030, 1, 1), "failures": 0}, "su-plentific": {"due_at": datetime(2030, 1, 1), "failures": 0}}
        self.assertEqual(list(run.unforced(states, sources, "revolut")), ["su-plentific"])
        self.assertEqual(run.unforced(states, sources, "funnel"), states)
        self.assertEqual(run.unforced(states, sources, "re"), states)
        self.assertEqual(run.unforced(states, sources, None), states)
        self.assertEqual(list(run.unforced(states, sources, "board:revolut, plentific")), [])             # several companies, board: ignored


class FirstPartyAgeInTheWebPass(unittest.TestCase):
    """D111: age is not a reason to drop a job from an employer's own board; a job dropped for age before comes back."""

    def stale(self):
        return {"id": "p1", "title": "Project Manager", "location": "London (hybrid)", "url": "https://x/p1", "posted": date(2026, 7, 14), "content": None}

    def test_suppression_ignores_age_for_a_first_party_board_only(self):
        job = self.stale()
        self.assertEqual(suppress.reason(job, date(2026, 10, 4), "Scale-Up"), "stale")
        self.assertIsNone(suppress.reason(job, date(2026, 10, 4), "Scale-Up", first_party=True))
        self.assertEqual(suppress.reason({**job, "title": "Senior Software Engineer"}, date(2026, 10, 4), "Scale-Up", first_party=True), "off_target_title")

    def test_an_unchanged_job_suppressed_for_age_is_admitted_on_the_next_pass(self):
        job = self.stale()
        previous = {"p1": (diff.material_hash(job), "SUPPRESSED")}
        listing = lister.Listing(lister.COMPLETE, [job])
        employer = {"id": "s", "kind": "ashby", "tier": "employer"}
        outcome = run.plan(employer, listing, previous, datetime(2026, 10, 4), 10, lane="Scale-Up")
        self.assertEqual([j["id"] for j in outcome.admit], ["p1"])
        aggregator = {"id": "s", "kind": "ashby", "tier": "staffing"}
        self.assertEqual(run.plan(aggregator, listing, previous, datetime(2026, 10, 4), 10, lane="Scale-Up").admit, [])


class StoredJobPutRight(unittest.TestCase):
    """D111: a job already stored whose listed title or place changed is corrected, judged again, and an exclusion that rested on the wrong place is lifted."""

    class Cur:
        def __init__(self):
            self.sql, self.rowcount, self.row = [], 1, (7,)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=()):
            sql % tuple("x" for _ in params)                    # survives driver formatting
            self.sql.append((sql, params))

        def fetchone(self):
            return self.row

    class Conn:
        def __init__(self, cur):
            self.cur = cur

        def cursor(self):
            return self.cur

    def test_a_changed_stored_job_is_corrected_and_its_score_cleared(self):
        cur = self.Cur()
        job = {"id": "p1", "title": "Delivery Manager", "location": "London", "url": "https://x/p1", "posted": None, "content": None}
        source = {"id": "su-stream", "kind": "stream_html", "tier": "employer", "company": "Stream"}
        with mock.patch.object(run.intake, "add_job", return_value=("key", False)):
            run.SqlRepo._admit(self.Conn(cur), source, job, datetime(2026, 10, 4), "Scale-Up")
        statements = [s for s, _ in cur.sql]
        update = next(s for s in statements if s.startswith("UPDATE v7_jobs SET title"))
        self.assertIn("IF(status='EXCLUDED_FIT' AND unresolved_reason='lane_exclude', 'READY', status)", update)
        self.assertIn("notion_page_id IS NULL", update)
        self.assertTrue(any(s.startswith("DELETE FROM v7_job_fit") for s in statements))
