"""D216 (LIFE-76 PR 2): conditional GET and the whole-board fingerprint skip. Production SQL over the sqlite kit; only the provider is faked. No network."""
from datetime import datetime, timedelta
import json
import unittest
from unittest import mock

from lifeos.platform import http, limits
from lifeos.sources.web import lister, run

NOW = datetime(2026, 10, 19, 6, 0)
BOARD = {"id": "gh-acme", "kind": "greenhouse", "slug": "acme", "company": "Acme"}
GH = {"jobs": [{"id": 1, "title": "Technical Program Manager", "location": {"name": "Remote - US"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                "first_published": "2026-10-18", "content": "Lead delivery"}]}


class Page:
    def __init__(self, status, body="", validators=None):
        self.status, self.html, self.validators = status, body, validators or {}


class Provider:
    """A fake ATS API: answers 304 only to the validators it handed out, records the headers it received."""
    def __init__(self, body=GH, etag='"v1"', honours=True, status=200):
        self.body, self.etag, self.honours, self.status, self.seen = body, etag, honours, status, []

    def __call__(self, url, **kw):
        headers = kw.get("headers") or {}
        self.seen.append(headers)
        if self.status != 200:
            return Page(self.status)
        if self.honours and headers.get("If-None-Match") == self.etag:
            return Page(304, validators={"etag": self.etag})
        return Page(200, json.dumps(self.body), {"etag": self.etag, "last_modified": "Sun, 19 Oct 2026 05:00:00 GMT"} if self.etag else {})


class Lister(unittest.TestCase):
    def test_validators_are_sent_only_when_the_run_asks_and_a_304_to_them_is_unchanged(self):
        provider = Provider()
        first = lister.list_source(BOARD, provider)
        self.assertEqual((first.status, first.validators["etag"], provider.seen[-1]), (lister.COMPLETE, '"v1"', {}))
        again = lister.list_source({**BOARD, "if_etag": '"v1"', "if_modified": first.validators["last_modified"]}, provider)
        self.assertEqual(again.status, lister.UNCHANGED)
        self.assertEqual(provider.seen[-1], {"If-None-Match": '"v1"', "If-Modified-Since": "Sun, 19 Oct 2026 05:00:00 GMT"})

    def test_an_unsupported_host_answers_a_full_body_and_a_304_nobody_asked_for_fails(self):
        self.assertEqual(lister.list_source({**BOARD, "if_etag": '"v1"'}, Provider(honours=False)).status, lister.COMPLETE)    # no validator support: 200 full body, read as usual
        self.assertEqual(lister.list_source(BOARD, Provider(etag=None)).validators, {})
        stray = lister.list_source(BOARD, lambda url, **kw: Page(304))
        self.assertEqual((stray.status, stray.reason), (lister.FAILED, "ambiguous_304"))

    def test_errors_and_other_kinds_are_unchanged(self):
        for status, why in ((403, "blocked_403"), (429, "rate_limited_429"), (0, "network"), (500, "http_500")):
            got = lister.list_source({**BOARD, "if_etag": '"v1"'}, Provider(status=status))
            self.assertEqual((got.status, got.reason), (lister.FAILED, why), status)
        seen = []
        lister.list_source({"id": "sr", "kind": "smartrecruiters", "slug": "acme", "if_etag": '"v1"'}, lambda url, **kw: seen.append(kw.get("headers")) or Page(500))
        self.assertTrue(all("If-None-Match" not in (h or {}) for h in seen))            # a paged board is never conditional

    def test_http_reads_the_validators_of_an_answer(self):
        self.assertEqual(http._validators({"ETag": ' "abc" ', "Last-Modified": "Sun, 19 Oct 2026 05:00:00 GMT"}),
                         {"etag": '"abc"', "last_modified": "Sun, 19 Oct 2026 05:00:00 GMT"})
        self.assertEqual(http._validators({"ETag": "x" * 300}), {})
        self.assertEqual(http._validators(None), {})


class Skip(unittest.TestCase):
    """The board pass over the real SqlRepo statements: quiet reads earn a skip; every WEB_FULL_EVERY-th read is a full diff; a change resets the streak."""

    def setUp(self):
        from tests.kit.jobsdb import JobsDB
        self.db = JobsDB()
        self.addCleanup(self.db.raw.close)

    def tick(self, provider, day):
        reads = []
        repo = run.SqlRepo()
        original = repo.items
        repo.items = lambda sid: reads.append(sid) or original(sid)
        with mock.patch.object(run.store, "connect", return_value=self.db), mock.patch.object(run.store, "ensure_schema"):
            counts = run.run(40, True, NOW + timedelta(days=2 * day), lambda s: lister.list_source(s, provider), repo, [BOARD])     # 2 days apart: always due, whatever the backoff
        row = self.db.raw.execute("SELECT unchanged_runs, etag, due_at, last_complete_at FROM v7_sources WHERE source_id='gh-acme'").fetchone()
        return counts, bool(reads), row

    def test_quiet_reads_skip_and_every_fourth_read_is_full(self):
        provider = Provider()
        seen = []
        for day in range(6):
            counts, read_items, (runs, etag, due, last) = self.tick(provider, day)
            seen.append((counts["added"], counts["not_modified"], read_items, runs, bool(provider.seen[-1])))
            self.assertEqual((etag, last), ('"v1"', NOW + timedelta(days=2 * day)))       # alerts read last_complete_at: a skip still counts as a complete read
        self.assertEqual(seen, [
            (1, 0, True, 0, False),    # first read: a job added, not quiet
            (0, 0, True, 1, False),    # a quiet full read earns the first skip
            (0, 1, False, 2, True),    # 304 to our validator: no item read
            (0, 1, False, 3, True),
            (0, 1, False, 4, True),
            (0, 0, True, 5, False)])   # the 4th read after the last full one is a full diff again, no validators sent

    def test_an_identical_listing_skips_without_validator_support_and_a_change_resets(self):
        provider = Provider(honours=False)
        for day in range(3):
            counts, read_items, (runs, *_ ) = self.tick(provider, day)
        self.assertEqual((counts["same_frontier"], read_items, runs), (1, False, 2))
        provider.body = {"jobs": GH["jobs"] + [{**GH["jobs"][0], "id": 2, "absolute_url": "https://boards.greenhouse.io/acme/jobs/2"}]}
        counts, read_items, (runs, *_ ) = self.tick(provider, 3)
        self.assertEqual((counts["added"], read_items, runs), (1, True, 0))             # a different board: full diff, streak reset

    def test_a_quiet_board_backs_off_up_to_three_times_and_a_change_returns_to_normal(self):
        periods = []
        for quiet in (0, 1, 2, 3, 4, 9):
            first = run.next_due("b", NOW, True, 0, 0, 6, quiet)
            periods.append(run.next_due("b", first, True, 0, 0, 6, quiet) - first)       # read on its phase: the next read is exactly one period later
            self.assertTrue(periods[-1] / 2 <= first - NOW < periods[-1] * 3 / 2)
        self.assertEqual(periods, [timedelta(hours=h) for h in (6, 6, 12, 18, 18, 18)])
        self.assertEqual(limits.WEB_BACKOFF_MAX, 3)

    def test_boards_keep_a_fixed_phase_spread_over_the_day(self):
        due = [run.next_due(f"gh-{i}", NOW, True, 0, 0, 24) for i in range(240)]
        self.assertEqual({d.time() for d in due}, {run.next_due(f"gh-{i}", NOW + timedelta(minutes=7), True, 0, 0, 24).time() for i in range(240)})
        hours = [sum(1 for d in due if d.hour == h) for h in range(24)]
        self.assertLess(max(hours), 25)                                             # ~10 an hour, never bunched where the reads happened
