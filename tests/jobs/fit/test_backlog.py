"""D219 (LIFE-41, Jim): the Cloud Fit backlog batch. Production SQL over the sqlite kit, the real Fit-stage promotion and the production request builder; only the Oracle
endpoint (and the Ledger page read) are fakes. Nothing here calls a model, Notion or the network."""
import json
from unittest import mock

from lifeos.jobs import fit_sync, publish
from lifeos.jobs.fit import backlog, cloud, edge, stage
from tests.jobs.fit.test_cloud import Base, PROFILE, T
from tests.jobs.fit.test_edge import RECEIPT, Endpoint, refusal, reply

USAGE = {"input_tokens": 1389, "cached_input_tokens": 0, "cache_write_tokens": 0, "output_tokens": 69, "reasoning_tokens": "unavailable", "total_tokens": 1458}
ENV = {"V7_CLOUD_FIT_PROFILE_REF": "0123456789abcdef", "IMESSAGE_ALERTS_HOST": "oracle.example", "V7_FIT_GATE": "true", "V7_CLOUD_FIT_BACKLOG_MAX": "10",
       "V7_CLOUD_FIT": "off"}                                                                   # the batch never reads V7_CLOUD_FIT: off here proves it


def measured(data, score=90, **kw):
    return reply(data, receipt={**RECEIPT, "usage": USAGE}, judgment_raw=json.dumps({"score": score, "rationale": "Aligned on delivery ownership.", "confidence": "high"}), **kw)


class Batch(Base):
    def batch(self, limit, endpoint, environ=ENV, ledger=None, live=True):
        with mock.patch.object(stage.store, "connect", return_value=self.db), mock.patch.object(stage.fit_profile, "load", return_value=PROFILE), \
                mock.patch.object(stage.sponsors, "load", return_value=None), mock.patch.object(edge, "exchange", endpoint), \
                mock.patch.object(backlog.ledger, "verify", return_value=backlog.ledger.OK), \
                mock.patch.object(backlog.guard, "protection", side_effect=ledger or (lambda client, page_id, company, title, opportunities=(): None)):
            self.client = mock.Mock()                                                           # the Notion client: records every call, answers nothing
            return backlog.run(limit, live, environ, client=self.client)

    def asked(self, endpoint):
        return [json.loads(f.split(b"\n", 1)[1])["request"]["title"] for f in endpoint.sent]

    def titled(self, job_id, title, score=80, admission="ADMIT", **kw):
        self.job(job_id, admission=admission, **kw)
        self.db.raw.execute("UPDATE v7_jobs SET title=? WHERE id=?", (title, job_id))
        self.db.raw.execute("UPDATE v7_job_fit SET score=? WHERE job_id=?", (score, job_id))

    def status(self, job_id):
        return self.row(job_id, ("cloud_status",))[0]


class BoundedBatch(Batch):
    def test_several_distinct_jobs_in_one_batch_most_useful_first_each_once_and_measured(self):
        self.titled(1, "Program Manager A", score=75)
        self.titled(2, "Program Manager B", score=88)
        self.titled(3, "Program Manager C", score=95, admission="REVIEW")
        self.titled(4, "Program Manager D", score=92)
        self.titled(5, "Program Manager E", score=70)
        endpoint = Endpoint(measured)
        counts = self.batch(3, endpoint)
        self.assertEqual(self.asked(endpoint), ["Program Manager D", "Program Manager B", "Program Manager A"])    # Admit before Review, higher static Fit first
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], counts["cloud_ok"], counts["cloud_promoted"]), ("OK", 3, 3, 3))
        self.assertEqual((counts["cloud_tokens_input"], counts["cloud_tokens_output"], counts["cloud_tokens_total"], counts["cloud_usage_unavailable"]), (4167, 207, 4374, 0))
        self.assertEqual(counts["remaining_before"]["unpublished_admit"], 4)
        self.assertEqual(counts["remaining_after"], {"unpublished_admit": 1, "unpublished_review": 1, "published_admit": 0, "published_review": 0})
        self.assertEqual([self.status(i) for i in (1, 2, 3, 4, 5)], ["CANARY", "CANARY", None, "CANARY", None])
        again = Endpoint(measured)
        self.batch(10, again)                                                                    # a second batch resumes: only the two never-asked jobs
        self.assertEqual(sorted(self.asked(again)), ["Program Manager C", "Program Manager E"])

    def test_nothing_is_asked_without_jims_ceiling_and_the_request_is_bounded_by_it(self):
        self.titled(1, "Program Manager A")
        for env in ({**ENV, "V7_CLOUD_FIT_BACKLOG_MAX": ""}, {**ENV, "V7_CLOUD_FIT_BACKLOG_MAX": "ten"}, {k: v for k, v in ENV.items() if k != "V7_CLOUD_FIT_BACKLOG_MAX"}):
            endpoint = Endpoint(measured)
            counts = self.batch(5, endpoint, environ=env)
            self.assertEqual((counts["cloud_fit"], counts["failed"], endpoint.sent), ("DEGRADED(BACKLOG_NOT_AUTHORIZED)", 1, []))
        for i in range(2, 6):
            self.titled(i, f"Program Manager {i}")
        endpoint = Endpoint(measured)
        counts = self.batch(50, endpoint, environ={**ENV, "V7_CLOUD_FIT_BACKLOG_MAX": "2"})
        self.assertEqual((counts["backlog_calls_allowed"], counts["cloud_calls"], len(endpoint.sent)), (2, 2, 2))
        self.assertEqual(self.batch(0, Endpoint(measured))["cloud_fit"], "DEGRADED(BACKLOG_NOT_AUTHORIZED)")
        capped = self.batch(10 ** 6, Endpoint(measured), environ={**ENV, "V7_CLOUD_FIT_BACKLOG_MAX": "999"}, live=False)
        self.assertEqual(capped["backlog_calls_allowed"], cloud.BACKLOG_CALLS_MAX)               # the code's hard ceiling holds whatever is asked or configured

    def test_a_dry_run_counts_the_backlog_and_asks_nothing(self):
        self.titled(1, "Program Manager A")
        endpoint = Endpoint(measured)
        counts = self.batch(5, endpoint, live=False)
        self.assertEqual((counts["cloud_fit"], counts["remaining_before"]["unpublished_admit"], endpoint.sent), ("dry_run", 1, []))


class Concurrency(Batch):
    def test_a_second_worker_never_asks_a_claimed_job_and_oracles_busy_releases_its_own_claim(self):
        self.titled(1, "Program Manager A", score=90)
        self.titled(2, "Program Manager B", score=85)
        second = {}

        def first_worker(data):
            if not second:                                                                      # while job 1 is in flight, a second worker starts its own batch
                inner = Endpoint(lambda d: refusal(d, status="BUSY"))                            # Oracle: one request in flight, a second gets BUSY (no model call)
                second["counts"] = self.batch(5, inner)
                second["asked"] = self.asked(inner)
            return measured(data)
        endpoint = Endpoint(first_worker)
        self.batch(1, endpoint)
        self.assertEqual(self.asked(endpoint), ["Program Manager A"])
        self.assertEqual(second["asked"], ["Program Manager B"])                                # never the claimed job 1
        self.assertEqual((second["counts"]["cloud_fit"], second["counts"]["cloud_ok"]), ("DEGRADED(FIT_EDGE_BUSY)", 0))
        self.assertEqual((self.status(1), self.status(2)), ("CANARY", None))                    # B's claim released: still askable, no model ran for it
        later = Endpoint(measured)
        self.batch(5, later)
        self.assertEqual(self.asked(later), ["Program Manager B"])

    def test_two_workers_that_picked_the_same_job_ask_it_once(self):
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        self.titled(2, "Program Manager B", score=70)

        def other_worker_claims_first(client, page_id, company, title, opportunities=()):
            self.db.raw.execute("UPDATE v7_job_fit SET cloud_status='CLAIMED', cloud_run_id='claim:other' WHERE job_id=1 AND cloud_status IS NULL")
            return None                                                                         # both picked job 1; the other worker's claim lands first
        endpoint = Endpoint(measured)
        counts = self.batch(5, endpoint, ledger=other_worker_claims_first)
        self.assertEqual((self.asked(endpoint), counts["cloud_claim_lost"]), (["Program Manager B"], 1))
        self.assertEqual(self.row(1, ("cloud_status", "cloud_run_id")), ("CLAIMED", "claim:other"))   # untouched: the other worker owns it

    def test_a_lost_claim_is_never_asked_and_a_lost_write_is_a_named_conflict(self):
        self.titled(1, "Program Manager A")
        self.db.raw.execute("UPDATE v7_job_fit SET cloud_status='CLAIMED', cloud_run_id='claim:other' WHERE job_id=1")
        endpoint = Endpoint(measured)
        counts = self.batch(5, endpoint)
        self.assertEqual((endpoint.sent, counts["cloud_calls"]), ([], 0))
        self.setUp()
        self.titled(1, "Program Manager A")

        def stolen(data):
            self.db.raw.execute("UPDATE v7_job_fit SET cloud_run_id='claim:other' WHERE job_id=1")   # another writer took the row while the answer was in flight
            return measured(data)
        counts = self.batch(5, Endpoint(stolen))
        self.assertEqual((counts["cloud_fit"], counts["cloud_ok"], counts["cloud_promoted"]), ("DEGRADED(CANARY_WRITE_CONFLICT)", 0, 0))
        self.assertEqual(self.row(1, ("score", "cloud_status")), (80, "CLAIMED"))               # nothing visible changed; never re-asked by a batch


class Interruption(Batch):
    def test_an_interrupted_batch_keeps_every_finished_answer_and_never_repeats_a_call(self):
        for i, title in enumerate(("A", "B", "C", "D"), start=1):
            self.titled(i, f"Program Manager {title}", score=90 - i)
        calls = []

        def crashes_on_third(data):
            calls.append(data)
            if len(calls) == 3:
                raise RuntimeError("runner killed mid-call")                                    # not an Oracle answer: the process dies here
            return measured(data)
        with self.assertRaises(RuntimeError):
            self.batch(4, Endpoint(crashes_on_third))
        self.assertEqual([self.status(i) for i in (1, 2, 3, 4)], ["CANARY", "CANARY", "CLAIMED", None])   # two answers kept; the third may have run: held, not re-asked
        resumed = Endpoint(measured)
        counts = self.batch(4, resumed)
        self.assertEqual(self.asked(resumed), ["Program Manager D"])
        self.assertEqual(counts["cloud_promoted"], 1)

    def test_a_daily_limit_or_quota_refusal_stops_the_batch_keeps_finished_work_and_requeues_the_refused_job(self):
        for i in (1, 2, 3):
            self.titled(i, f"Program Manager {i}", score=90 - i)
        seen = []

        def limit_after_one(data):
            seen.append(data)
            return measured(data) if len(seen) == 1 else refusal(data, "DAILY_LIMIT")
        counts = self.batch(3, Endpoint(limit_after_one))
        self.assertEqual((counts["cloud_fit"], counts["cloud_ok"], counts["cloud_calls"], len(seen)), ("DEGRADED(FIT_EDGE_REFUSED_DAILY_LIMIT)", 1, 2, 2))
        self.assertEqual([self.status(i) for i in (1, 2, 3)], ["CANARY", None, None])
        self.assertEqual(counts["failed"], 1)

    def test_a_timeout_is_recorded_and_never_retried(self):
        self.titled(1, "Program Manager A")
        self.titled(2, "Program Manager B", score=70)
        endpoint = Endpoint(lambda data: reply(data, status="TIMEOUT", judgment_raw=None))
        counts = self.batch(5, endpoint)
        self.assertEqual((counts["cloud_fit"], len(endpoint.sent), self.status(1), self.status(2)), ("DEGRADED(FIT_EDGE_TIMEOUT)", 1, "DEGRADED", None))

    def test_missing_token_counts_stop_the_batch_after_keeping_that_answer(self):
        self.titled(1, "Program Manager A")
        self.titled(2, "Program Manager B", score=70)
        endpoint = Endpoint(lambda data: reply(data))                                           # usage "unavailable": an upgraded OpenClaw without the pass-through
        counts = self.batch(5, endpoint)
        self.assertEqual((counts["cloud_fit"], len(endpoint.sent), counts["cloud_promoted"], counts["cloud_usage_unavailable"]), ("DEGRADED(USAGE_UNAVAILABLE)", 1, 1, 1))
        self.assertEqual((self.status(1), self.status(2)), ("CANARY", None))


class PublishedJobs(Batch):
    def test_an_admitted_published_job_gets_the_cloud_fit_through_fit_sync_and_no_human_field_is_written(self):
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        self.db.raw.execute("UPDATE v7_jobs SET fit_synced_at=? WHERE id=1", (T,))
        counts = self.batch(5, Endpoint(measured))
        self.assertEqual(counts["cloud_promoted"], 1)
        score, line, admission, status = self.row(1, ("score", "line", "admission", "cloud_status"))
        self.assertEqual((score, admission, status), (90, "ADMIT", "CANARY"))
        self.assertTrue(line.startswith("Cloud Fit 90% (gpt-6-luna): "))
        self.assertEqual(self.db.raw.execute("SELECT status, notion_page_id FROM v7_jobs WHERE id=1").fetchone(), ("PUBLISHED", "page-1"))
        self.assertEqual(counts["ledger_pages_pending"], 0)                                     # the batch synced the page itself (see the backlog-only test)
        with mock.patch.object(fit_sync.store, "connect", return_value=self.db):
            again = fit_sync.run(mock.Mock(), live=True, gated=True)
        self.assertEqual(again["due"], 0)                                                        # nothing left for the next tick's sync: written exactly once

    def test_a_backlog_only_dispatch_updates_the_existing_ledger_page_itself_with_machine_fields_only(self):
        """Review of #350: the publish step (which runs fit_sync) does not run on a cloud_backlog-only dispatch; the batch must reach the page by itself."""
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        self.titled(2, "Program Manager B", score=70)                                           # an unpublished job promoted in the same batch: no page to write
        self.db.raw.execute("UPDATE v7_jobs SET fit_synced_at=? WHERE id=1", (T,))
        counts = self.batch(5, Endpoint(measured))
        self.assertEqual((counts["cloud_fit"], counts["cloud_promoted"], counts["ledger_pages_promoted"], counts["ledger_pages_pending"], counts["failed"]), ("OK", 2, 1, 0, 0))
        patches = [c.args for c in self.client.call.call_args_list if c.args[0] == "PATCH"]
        self.assertEqual([(m, p) for m, p, _ in patches], [("PATCH", "/pages/page-1")])                # exactly the one existing page; nothing created, nothing trashed
        props = patches[0][2]["properties"]
        self.assertEqual(props["LIFE OS Fit"], {"number": 90})
        self.assertTrue(props["Why It Fits"]["rich_text"][0]["text"]["content"].startswith("Cloud Fit 90% (gpt-6-luna): "))
        self.assertEqual(props["Admission Status"], {"select": {"name": "Admitted"}})
        self.assertFalse({"Applied", "Applied On", "Saturn Decision", "Job"} & set(props))      # human state and the title are never written
        self.assertFalse([c for c in self.client.call.call_args_list if c.args[0] in ("POST", "DELETE")] or any("in_trash" in (c.args[2] if len(c.args) > 2 else {}) for c in self.client.call.call_args_list))
        synced, scored = self.db.raw.execute("SELECT j.fit_synced_at, f.scored_at FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id WHERE j.id = 1").fetchone()
        self.assertGreaterEqual(str(synced), str(scored))

    def test_a_page_the_sync_could_not_write_is_a_named_failure_and_the_score_is_kept(self):
        from lifeos.platform.notion_client import NotionError
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        with mock.patch.object(backlog.fit_sync, "run", side_effect=lambda client, live, gated=False, limit=150: {"due": 1, "synced": 0, "failed": 1}):
            counts = self.batch(5, Endpoint(measured))
        self.assertEqual((counts["cloud_fit"], counts["ledger_pages_pending"], counts["failed"]), ("DEGRADED(LEDGER_SYNC_INCOMPLETE)", 1, 1))
        self.assertEqual(self.row(1, ("score", "cloud_status")), (90, "CANARY"))                # kept: the next sync (any tick) retries the page
        self.assertTrue(NotionError)

    def test_a_held_published_job_is_never_synced(self):
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        counts = self.batch(5, Endpoint(lambda d: measured(d, score=41)))
        self.assertEqual((counts["cloud_held"], counts["ledger_pages_promoted"]), (1, 0))
        self.assertFalse([c for c in self.client.call.call_args_list if c.args[0] == "PATCH"])

    def test_a_published_job_the_cloud_would_exclude_is_held_privately_and_its_page_is_untouched(self):
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        before = self.row(1, ("score", "decision", "line", "admission", "scored_at"))
        counts = self.batch(5, Endpoint(lambda d: measured(d, score=41)))
        self.assertEqual((counts["cloud_held"], counts["cloud_promoted"]), (1, 0))
        self.assertEqual(self.row(1, ("score", "decision", "line", "admission", "scored_at")), before)
        self.assertEqual(self.row(1, ("cloud_status", "cloud_reason", "cloud_score")), ("HELD", "PUBLISHED_WOULD_EXCLUDE", 41))
        self.assertEqual(self.db.raw.execute("SELECT status, notion_page_id FROM v7_jobs WHERE id=1").fetchone(), ("PUBLISHED", "page-1"))   # the audit has nothing to trash

    def test_applied_or_unreadable_ledger_pages_use_no_call_and_stay_askable(self):
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        self.titled(2, "Program Manager B", status="PUBLISHED", page="page-2", score=70)
        self.titled(3, "Program Manager C", score=60)
        held = {"page-1": "applied", "page-2": "unreadable"}
        endpoint = Endpoint(measured)
        counts = self.batch(5, endpoint, ledger=lambda client, page_id, company, title, opportunities=(): held.get(page_id))
        self.assertEqual(self.asked(endpoint), ["Program Manager C"])
        self.assertEqual(counts["cloud_skip_reasons"], {"LEDGER_APPLIED": 1, "LEDGER_UNREADABLE": 1})
        self.assertEqual((self.status(1), self.status(2)), (None, None))
        self.assertEqual(counts["remaining_after"]["published_admit"], 2)

    def test_excluded_and_already_scored_jobs_are_never_picked(self):
        self.titled(1, "Program Manager A", status="EXCLUDED_FIT", admission="EXCLUDE")
        self.titled(2, "Program Manager B", admission="EXCLUDE")
        self.titled(3, "Program Manager C", exclusion="junior")
        self.titled(4, "Program Manager D", cloud_status="CANARY")
        endpoint = Endpoint(measured)
        counts = self.batch(5, endpoint)
        self.assertEqual((endpoint.sent, counts["cloud_calls"]), ([], 0))
        self.assertEqual(self.db.raw.execute("SELECT status FROM v7_jobs WHERE id=1").fetchone()[0], "EXCLUDED_FIT")

    def test_a_published_unadmitted_cloud_result_is_never_republished(self):
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        self.batch(5, Endpoint(lambda d: measured(d, score=41)))
        with mock.patch.object(publish, "_now", return_value=T):
            rows, _ = publish._pick(self.db, 10, gated=True)
        self.assertEqual(rows, [])                                                               # publish creates pages only for READY jobs: no duplicate page
