import urllib.error
import urllib.request
import unittest
from unittest import mock

from lifeos.jobs.resolve import stage
from lifeos.jobs.resolve.aggregators import dice
from lifeos.platform import limits
from tests.kit.db import FakeConn
from tests.kit.http import Response, ScriptedOpener, http_error


TRACK1 = "https://elinks.dice.com/a/sc/example-one"
TRACK2 = "https://elinks.dice.com/a/sc/example-two"
JOB = "https://www.dice.com/job-detail/EXAMPLE123?campaign=private"
JOB_HTML = "<html><title>Example position</title><p>Apply for this job</p></html>"


def redirect(location, status=302):
    return http_error(urllib.request.Request(TRACK1), status, headers={"Location": location})


class DiceResolverTests(unittest.TestCase):
    def resolve_with(self, opener, url=TRACK1):
        with mock.patch.object(dice.http, "_OPENER", opener):
            return dice.resolve(url)

    def test_redirect_to_job_page_normalizes_host_path_and_drops_query(self):
        opener = ScriptedOpener(redirect(JOB), Response(JOB_HTML.encode()))
        result = self.resolve_with(opener)
        self.assertEqual(result, {"outcome": "landed", "via": "tracking_redirect", "kind": "aggregator",
                                  "url": "https://www.dice.com/job-detail/EXAMPLE123"})
        self.assertEqual(len(opener.urls), 2)

    def test_redirect_outside_dice_is_rejected(self):
        opener = ScriptedOpener(redirect("https://example.com/job-detail/EXAMPLE123"), Response(JOB_HTML.encode()))
        self.assertEqual(self.resolve_with(opener)["outcome"], "not_a_job_page")

    def test_job_404_or_410_is_closed_and_explicitly_closed_page_is_closed(self):
        for status in (404, 410):
            missing = ScriptedOpener(redirect("https://www.dice.com/job-detail/EXPIRED"), Response(b"", status=status))
            self.assertEqual(self.resolve_with(missing)["outcome"], "closed")
        closed = ScriptedOpener(redirect("https://dice.com/jobs/detail/EXPIRED?x=1"),
                                Response(b"<p>This job is closed</p>"))
        self.assertEqual(self.resolve_with(closed)["outcome"], "closed")

    def test_tracking_404_is_an_expired_link(self):
        expired = ScriptedOpener(Response(b"", status=404))
        self.assertEqual(self.resolve_with(expired)["outcome"], "expired_link")

    def test_rate_limit_stops_requests_for_the_rest_of_the_batch(self):
        opener = ScriptedOpener(Response(b"", status=429))
        with mock.patch.object(dice.http, "_OPENER", opener), mock.patch.object(dice.time, "sleep"):
            results = dice.make_resolver()([(1, TRACK1), (2, TRACK2), (3, TRACK1)])
        self.assertEqual([r["outcome"] for r in results], ["rate_limited", "deferred", "deferred"])   # untried rows are not attempts
        self.assertEqual(len(opener.urls), 1)

    def test_network_error_stays_pending_and_never_closes_or_duplicates(self):
        opener = ScriptedOpener(urllib.error.URLError("offline"))
        result = self.resolve_with(opener)
        self.assertEqual(result["outcome"], "network_error")
        statements = []
        connection = FakeConn(handler=lambda sql, args, cursor: statements.append(sql))
        self.assertEqual(stage.apply_result(connection, 7, result), "pending")
        self.assertTrue(any("resolve_attempts=resolve_attempts+%s" in sql for sql in statements))
        self.assertFalse(any("status='CLOSED'" in sql or "status='DUPLICATE'" in sql for sql in statements))

    def test_different_tracking_links_for_one_job_hit_existing_duplicate_path(self):
        opener = ScriptedOpener(redirect(JOB), Response(JOB_HTML.encode()),
                                redirect("https://dice.com/jobs/detail/EXAMPLE123?other=x"), Response(JOB_HTML.encode()))
        with mock.patch.object(dice.http, "_OPENER", opener), mock.patch.object(dice.time, "sleep"):
            results = dice.make_resolver()([(1, TRACK1), (2, TRACK2)])
        self.assertEqual(results[0]["url"], results[1]["url"])
        final_urls = {}

        def database(sql, args, cursor):
            if sql.startswith("SELECT id FROM v7_jobs WHERE final_apply_url"):
                cursor.row = next(((job_id,) for job_id, final in final_urls.items()
                                   if final == args[0] and job_id != args[1]), None)
            elif sql.startswith("UPDATE v7_jobs SET status='RESOLVED'"):
                final_urls[args[3]] = args[0]

        connection = FakeConn(handler=database)
        self.assertEqual(stage.apply_result(connection, 1, results[0]), "resolved")
        self.assertEqual(stage.apply_result(connection, 2, results[1]), "duplicate")
        self.assertEqual(len(final_urls), 1)

    def test_run_applies_per_run_cap_and_shared_deadline(self):
        fake_counts = {"picked": 0, "resolved": 0, "closed": 0, "duplicate": 0, "pending": 0,
                       "why": {"rate_limited": 0}}
        with mock.patch.object(dice.stage, "run_rows", return_value=fake_counts) as run_rows:
            counts = dice.run(10000, False)
        args, kwargs = run_rows.call_args
        self.assertEqual((args[0], args[1], args[2]), ("dice", limits.DICE_PER_RUN, False))
        self.assertEqual(kwargs["deadline_minutes"], limits.DICE_DEADLINE_MINUTES)
        self.assertLessEqual(limits.DICE_DEADLINE_MINUTES, 3)                    # Dice is low value: never a long job
        self.assertEqual(counts, {"picked": 0, "resolved": 0, "closed": 0, "duplicate": 0, "pending": 0, "rate_limited": 0})

    def test_gap_is_shared_between_batches(self):
        opener = ScriptedOpener(redirect(JOB), Response(JOB_HTML.encode()),
                                redirect(JOB), Response(JOB_HTML.encode()))
        resolver = dice.make_resolver()
        with mock.patch.object(dice.http, "_OPENER", opener), mock.patch.object(dice.time, "sleep") as sleep:
            resolver([(1, TRACK1)])
            resolver([(2, TRACK2)])
        self.assertEqual(sleep.call_count, 1)
        self.assertAlmostEqual(sleep.call_args.args[0], limits.DICE_GAP_SECONDS, delta=0.1)


if __name__ == "__main__":
    unittest.main()
