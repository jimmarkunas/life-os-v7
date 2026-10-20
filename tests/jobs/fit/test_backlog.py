"""D219 (LIFE-41, Jim): the Cloud Fit backlog batch. Production SQL over the sqlite kit, the real Fit-stage promotion and the production request builder; only the Oracle
endpoint (and the Ledger page read) are fakes. Nothing here calls a model, Notion or the network."""
import json
import threading
import time
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
                mock.patch.object(backlog.edge, "limits", side_effect=self.report), \
                mock.patch.object(backlog.guard, "protection", side_effect=ledger or (lambda client, page_id, company, title, opportunities=(): None)):
            self.client = mock.Mock()                                                           # the Notion client: records every call, answers nothing
            return backlog.run(limit, live, environ, client=self.client)

    reported = None

    def report(self, host, backlog=None, **kw):
        """LIFE-77: the batch's backlog report to Oracle, recorded instead of sent; set `report_fails` to make it fail."""
        if getattr(self, "report_fails", None):
            raise edge.EdgeError(self.report_fails)
        self.reported = (host, backlog)
        return {}

    def asked(self, endpoint):
        return [json.loads(f.split(b"\n", 1)[1])["request"]["title"] for f in endpoint.sent]

    def titled(self, job_id, title, score=80, admission="ADMIT", **kw):
        self.job(job_id, admission=admission, **kw)
        self.db.raw.execute("UPDATE v7_jobs SET title=? WHERE id=?", (title, job_id))
        self.db.raw.execute("UPDATE v7_job_fit SET score=? WHERE job_id=?", (score, job_id))

    def status(self, job_id):
        return self.row(job_id, ("cloud_status",))[0]

    def audit(self, protection=None):
        """The production gated precision audit over the same store: only Notion (the page trash) and the guard's page read are fakes."""
        from lifeos.jobs import audit
        client = mock.Mock()
        with mock.patch.object(audit.store, "connect", return_value=self.db), mock.patch.object(audit.store, "ensure_schema"), \
                mock.patch.object(audit.notion_client, "Client", return_value=client), mock.patch.object(audit.ledger, "verify", return_value=audit.ledger.OK), \
                mock.patch.object(audit.guard, "protection", side_effect=protection or (lambda client, page_id, company, title, opportunities=(): None)):
            counts = audit.run(100, True, environ={"V7_FIT_GATE": "true"})
        return counts, [c.args[1] for c in client.call.call_args_list if c.args[:1] == ("PATCH",) and c.args[2] == {"in_trash": True}]


class BudgetIsALimitNotAFailure(Batch):
    """LIFE-41 (first scheduled batch, run 39356515961): 19 of 20 calls fit the 480 s budget and the run was marked failed. A batch that stops at its time limit
    after real work is normal; only a batch with no time for even one call is a shortfall."""

    def test_reaching_the_budget_after_calls_is_ok_and_the_rest_wait(self):
        for i in (1, 2, 3):
            self.titled(i, f"Program Manager {i}", score=90 - i)
        endpoint = Endpoint(measured)
        with mock.patch.object(cloud, "remaining", side_effect=[1000, 1000, 0]):
            counts = self.batch(5, endpoint)
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], counts["cloud_budget_reached"], counts["failed"]), ("OK", 2, 1, 0))
        self.assertEqual(self.status(3), None)                                                            # unasked, unclaimed: the next run takes it

    def test_no_time_for_even_one_call_is_still_a_shortfall(self):
        self.titled(1, "Program Manager 1")
        with mock.patch.object(cloud, "remaining", return_value=0):
            counts = self.batch(5, Endpoint(measured))
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], counts["failed"]), ("DEGRADED(STAGE_BUDGET)", 0, 1))


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


class TargetedPilot(Batch):
    """LIFE-41 G6: Jim names one preapproved Ledger page; the batch asks that job or nothing, never the next one in rank."""
    PAGE = "3ed3c5a0-5926-8125-8c1d-f0628db65bc1".replace("3ed3c5a0", "0a0b0c0d")                 # an invented id in the Ledger's dashed form

    def env(self, page):
        return {**ENV, "V7_CLOUD_FIT_BACKLOG_PAGE": page}

    def test_the_named_page_is_asked_even_when_others_rank_higher_and_only_once(self):
        self.titled(1, "Program Manager A", score=99, status="PUBLISHED", page="page-1")
        self.titled(2, "Program Manager B", score=75, status="PUBLISHED", page=self.PAGE)
        self.db.raw.execute("UPDATE v7_jobs SET fit_synced_at=? WHERE id IN (1, 2)", (T,))
        endpoint = Endpoint(measured)
        counts = self.batch(5, endpoint, environ=self.env(self.PAGE.replace("-", "").upper()))
        self.assertEqual(self.asked(endpoint), ["Program Manager B"])
        self.assertEqual((counts["backlog_calls_allowed"], counts["backlog_targeted"], counts["cloud_calls"], counts["cloud_promoted"]), (1, 1, 1, 1))
        self.assertEqual((self.status(1), self.status(2)), (None, "CANARY"))
        self.assertNotIn(self.PAGE.replace("-", ""), json.dumps(counts))                          # the id never reaches the log

    def test_a_target_the_batch_would_not_ask_is_refused_with_no_call_and_nothing_else_is_asked(self):
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        self.titled(2, "Program Manager B", status="PUBLISHED", page=self.PAGE)
        self.db.raw.execute("UPDATE v7_job_fit SET cloud_status='HELD' WHERE job_id=2")         # already asked
        for page, why in ((self.PAGE, "ASKED_HELD"), ("f" * 32, "NOT_IN_STORE")):               # asked before; not in the store at all
            endpoint = Endpoint(measured)
            counts = self.batch(5, endpoint, environ=self.env(page))
            self.assertEqual((counts["cloud_fit"], counts["backlog_target_reason"], endpoint.sent, self.status(1)), ("DEGRADED(BACKLOG_TARGET_NOT_ELIGIBLE)", why, [], None))

    def test_the_refusal_names_the_first_predicate_the_target_fails(self):
        self.titled(1, "Program Manager A", status="PUBLISHED", page=self.PAGE)
        for change, why in (("UPDATE v7_job_descriptions SET source_kind='title_only' WHERE job_id=1", "SOURCE_TITLE_ONLY"),
                            ("UPDATE v7_job_fit SET exclusion='private' WHERE job_id=1", "EXCLUDED"),
                            ("UPDATE v7_job_fit SET admission='EXCLUDE' WHERE job_id=1", "ADMISSION_EXCLUDE"),
                            ("UPDATE v7_jobs SET status='CLOSED' WHERE id=1", "STATUS_CLOSED")):
            self.db.raw.execute(change)
            counts = self.batch(5, Endpoint(measured), environ=self.env(self.PAGE))
            self.assertEqual((counts["cloud_calls"], counts["backlog_target_reason"]), (0, why), change)
        self.assertEqual(cloud.target_reason([("PUBLISHED", "ADMIT", 1, 0, None, "ats_api")] * 2), "AMBIGUOUS")
        self.assertEqual(cloud.target_reason([("PUBLISHED", None, 1, 1, None, None)]), "NOT_SCORED")

    def test_a_malformed_target_is_refused_before_any_store_pick(self):
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        for page in ("not-a-page", "g" * 32, "1" * 31):
            endpoint = Endpoint(measured)
            counts = self.batch(5, endpoint, environ=self.env(page))
            self.assertEqual((counts["cloud_fit"], counts["failed"], endpoint.sent), ("DEGRADED(BACKLOG_TARGET_INVALID)", 1, []))

    def test_an_applied_target_page_uses_no_call(self):
        self.titled(1, "Program Manager A", status="PUBLISHED", page=self.PAGE)
        endpoint = Endpoint(measured)
        counts = self.batch(5, endpoint, environ=self.env(self.PAGE), ledger=lambda client, page_id, company, title, opportunities=(): backlog.guard.APPLIED)
        self.assertEqual((endpoint.sent, counts["cloud_calls"], self.status(1)), ([], 0, None))

    def test_without_a_target_the_ranked_backlog_is_unchanged(self):
        self.titled(1, "Program Manager A", score=75)
        self.titled(2, "Program Manager B", score=95)
        endpoint = Endpoint(measured)
        self.batch(1, endpoint, environ={**ENV, "V7_CLOUD_FIT_BACKLOG_PAGE": ""})
        self.assertEqual(self.asked(endpoint), ["Program Manager B"])


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
        endpoint = Endpoint(lambda data: reply(data, receipt={**RECEIPT, "usage": "unavailable"}))   # an upgraded OpenClaw without the pass-through
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

    def test_a_published_job_the_cloud_excludes_is_promoted_and_the_gated_audit_removes_its_page(self):
        """Jim (LIFE-41, Oct 20: "Apply the exclusion", replacing the D219 hold): the cloud verdict is final. The answer becomes the visible Fit, the page shows
        Excluded, and the next tick's audit trashes it and parks the job EXCLUDED_FIT, never published again."""
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        counts = self.batch(5, Endpoint(lambda d: measured(d, score=41)))
        self.assertEqual(counts["cloud_promoted"], 1)
        self.assertNotIn("cloud_held", counts)
        score, line, admission, status = self.row(1, ("score", "line", "admission", "cloud_status"))
        self.assertEqual((score, admission, status), (41, "EXCLUDE", "CANARY"))
        self.assertTrue(line.startswith("Cloud Fit 41% (gpt-6-luna): "))
        synced = [c.args[2]["properties"] for c in self.client.call.call_args_list if c.args[:2] == ("PATCH", "/pages/page-1")]
        self.assertEqual([p["Admission Status"]["select"]["name"] for p in synced], ["Excluded"])           # the page tells the truth until the audit removes it
        audited, trashed = self.audit()
        self.assertEqual((trashed, audited["excluded_cleared"]), (["/pages/page-1"], 1))
        self.assertEqual(self.db.raw.execute("SELECT status, notion_page_id, unresolved_reason FROM v7_jobs WHERE id=1").fetchone(), ("EXCLUDED_FIT", None, "excluded_fit"))

    def test_a_page_a_human_may_be_pursuing_keeps_its_page_after_a_cloud_exclusion(self):
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        self.batch(5, Endpoint(lambda d: measured(d, score=41)))
        audited, trashed = self.audit(lambda client, page_id, company, title, opportunities=(): "saturn_decision")
        self.assertEqual((trashed, audited["protected"]), ([], {"saturn_decision": 1}))
        self.assertEqual(self.db.raw.execute("SELECT status, notion_page_id FROM v7_jobs WHERE id=1").fetchone(), ("PUBLISHED", "page-1"))


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


class FailureReachesTheRun(Batch):
    """Career TL review of #350 (3b07c08): the batch step is continue-on-error, so its failure must be carried by the finish job's lane aggregation; a clean batch,
    and every run that does not ask for a batch (a tick, any other dispatch), must leave the run green."""

    def workflow(self):
        import pathlib
        import yaml
        data = yaml.safe_load((pathlib.Path(__file__).resolve().parents[3] / ".github" / "workflows" / "hourly.yml").read_text())
        steps = data["jobs"]["finish"]["steps"]
        return data, steps, next(s for s in steps if s.get("id") == "fit_cloud_backlog"), next(s for s in steps if s.get("id") == "lanes")

    def lanes(self, lanes, outcome):
        import os
        import pathlib
        import re
        import subprocess
        import tempfile
        script = re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", lambda m: outcome if m.group(1) == "steps.fit_cloud_backlog.outcome" else "success", lanes["run"])
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory, "out")
            done = subprocess.run(["bash", "-e", "-c", script], env={**os.environ, "GITHUB_OUTPUT": str(output)}, capture_output=True, text=True, check=False)
            return done.returncode, done.stdout.strip(), next(line for line in output.read_text().splitlines() if line.startswith("failed="))

    def test_a_failed_batch_fails_the_finish_job_and_names_the_stage_while_a_clean_or_absent_batch_stays_green(self):
        data, steps, step, lanes = self.workflow()
        self.assertTrue(step.get("continue-on-error"))                                           # later stages still run after a failed batch ...
        self.assertIn("python -m lifeos.run fit-cloud-backlog", step["run"])
        self.assertNotIn("|| true", step["run"])                                                 # ... and nothing swallows its exit code
        self.assertEqual((lanes.get("if"), data["jobs"]["finish"]["outputs"]["failed"]), ("${{ always() }}", "${{ steps.lanes.outputs.failed }}"))
        self.assertLess(steps.index(step), steps.index(lanes))
        self.assertEqual(self.lanes(lanes, "failure"), (1, "FAILED STAGES: fit_cloud_backlog", "failed=fit_cloud_backlog"))
        self.assertEqual(self.lanes(lanes, "success"), (0, "", "failed="))
        self.assertEqual(self.lanes(lanes, "skipped"), (0, "", "failed="))                       # a tick or any other dispatch never runs the step

    def test_the_stage_exits_1_on_a_degraded_batch_and_0_on_a_clean_one(self):
        import contextlib
        import io
        from lifeos import run as lifeos_run
        for counts, code in (({"cloud_fit": "DEGRADED(LEDGER_SYNC_INCOMPLETE)", "failed": 1}, 1), ({"cloud_fit": "DEGRADED(BACKLOG_NOT_AUTHORIZED)", "failed": 1}, 1),
                             ({"cloud_fit": "OK", "failed": 0}, 0)):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(backlog, "run", return_value=counts), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(lifeos_run.main(["fit-cloud-backlog", "--limit", "3", "--live"]), code, counts)
            self.assertEqual(err.getvalue().strip(), "FIT-CLOUD-BACKLOG FAILED: FIT_CLOUD_DEGRADED" if code else "")

    def test_a_real_degraded_batch_reaches_exit_1_end_to_end(self):
        import contextlib
        import io
        import os
        from lifeos import run as lifeos_run
        self.titled(1, "Program Manager A", status="PUBLISHED", page="page-1")
        real = backlog.run
        with mock.patch.object(stage.store, "connect", return_value=self.db), mock.patch.object(stage.fit_profile, "load", return_value=PROFILE), \
                mock.patch.object(stage.sponsors, "load", return_value=None), mock.patch.object(edge, "exchange", Endpoint(measured)), \
                mock.patch.object(backlog.ledger, "verify", return_value=backlog.ledger.OK), mock.patch.object(backlog.guard, "protection", return_value=None), \
                mock.patch.object(backlog.edge, "limits", side_effect=self.report), \
                mock.patch.object(backlog.fit_sync, "run", return_value={"due": 1, "synced": 0, "failed": 1}), mock.patch.dict(os.environ, ENV), \
                mock.patch.object(backlog, "run", side_effect=lambda limit, live: real(limit, live, os.environ, client=mock.Mock())), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = lifeos_run.main(["fit-cloud-backlog", "--limit", "3", "--live"])
        self.assertEqual((code, err.getvalue().strip()), (1, "FIT-CLOUD-BACKLOG FAILED: FIT_CLOUD_DEGRADED"))    # the batch's own failure -> exit 1 -> a failed step
        self.assertIn('"cloud_fit": "DEGRADED(LEDGER_SYNC_INCOMPLETE)"', out.getvalue())
        self.assertEqual(self.row(1, ("score", "cloud_status")), (90, "CANARY"))                # the score is kept; only the signal is a failure


class HeldAnswersAreApplied(Batch):
    """The jobs the D219 hold kept private (cloud_status HELD) get Jim's decision from their stored answer: no second model call, the same promotion."""

    def held(self, job_id, score=41, page="page-1"):
        self.titled(job_id, f"Program Manager {job_id}", status="PUBLISHED", page=page)
        self.db.raw.execute("UPDATE v7_job_fit SET cloud_status='HELD', cloud_reason='PUBLISHED_WOULD_EXCLUDE', cloud_score=?, cloud_confidence='high',"
                            " cloud_rationale='Thin delivery ownership.', cloud_model='gpt-6-luna' WHERE job_id=?", (score, job_id))

    def release(self):
        counts = {"gated": 0, "revived": 0}
        with mock.patch.object(stage.store, "connect", return_value=self.db):
            with self.db.cursor() as cursor:
                return stage.release_held(cursor, PROFILE, T.date(), True, counts, None)

    def test_each_held_answer_is_promoted_once_and_the_audit_then_removes_the_page(self):
        self.held(1)
        self.held(2, page="page-2")
        self.assertEqual(self.release(), (2, 0))
        for job_id in (1, 2):
            score, line, admission, status, reason = self.row(job_id, ("score", "line", "admission", "cloud_status", "cloud_reason"))
            self.assertEqual((score, admission, status, reason), (41, "EXCLUDE", "CANARY", None))
            self.assertEqual(line, "Cloud Fit 41% (gpt-6-luna): Thin delivery ownership.")
        self.assertEqual(self.release(), (0, 0))                                                                # nothing HELD is left: a no-op every later tick
        self.assertEqual(sorted(self.audit()[1]), ["/pages/page-1", "/pages/page-2"])

    def test_a_held_answer_that_cannot_be_promoted_stays_a_named_failure_and_is_never_held_or_asked_again(self):
        self.held(1)
        self.db.raw.execute("DELETE FROM v7_job_descriptions WHERE job_id=1")
        self.assertEqual(self.release(), (0, 1))
        self.assertEqual(self.row(1, ("cloud_status", "cloud_reason", "admission")), ("DEGRADED", cloud.PROMOTION_FAILED, "ADMIT"))
        self.assertEqual(self.release(), (0, 0))

    def test_the_fit_stage_applies_the_holds_only_when_live_and_gated(self):
        self.held(1)
        env = {**ENV, "V7_CLOUD_FIT": "off"}
        with mock.patch.object(stage.store, "connect", return_value=self.db), mock.patch.object(stage.store, "ensure_schema"), \
                mock.patch.object(stage.fit_profile, "load", return_value=PROFILE), mock.patch.object(stage.sponsors, "load", return_value=None):
            self.assertNotIn("cloud_held_released", stage.run(10, False, env))
            self.assertNotIn("cloud_held_released", stage.run(10, True, {**env, "V7_FIT_GATE": "false"}))
            self.assertEqual(self.row(1, ("cloud_status",)), ("HELD",))
            counts = stage.run(10, True, env)
        self.assertEqual((counts["cloud_held_released"], counts["cloud_held_failed"], counts["failed"]), (1, 0, 0))
        self.assertEqual(self.row(1, ("cloud_status", "admission")), ("CANARY", "EXCLUDE"))


def metered(used_5h, used_week=1, stop=50):
    return {"stop": stop, "windows": [{"label": "5h", "used": used_5h, "reset_at": "2026-10-20T07:12:19+00:00"},
                                      {"label": "Week", "used": used_week, "reset_at": "2026-10-24T21:26:54+00:00"}]}


class AccountAllowance(Batch):
    """LIFE-77 (Jim, Oct 19): Oracle reads the account's own allowance before each call and refuses QUOTA_RESERVE at Jim's 50 % stop. That is a normal stop for the
    batch; a missing policy or an unreadable meter is a named failure. Each call's reading becomes an estimate of what the allowance has left."""

    def test_the_reserve_is_a_clean_stop_the_refused_job_waits_and_the_estimate_is_logged(self):
        for i in (1, 2, 3, 4):
            self.titled(i, f"Program Manager {i}", score=90 - i)
        used = iter((47, 48, 49))

        def answer(data):
            u = next(used, None)
            return measured(data, quota=metered(u)) if u is not None else reply(data, status="REFUSED", reason="QUOTA_RESERVE", judgment_raw=None, receipt=None, quota=metered(50))
        counts = self.batch(10, Endpoint(answer))
        self.assertEqual((counts["cloud_fit"], counts["failed"], counts["cloud_quota_reserve"], counts["cloud_promoted"]), ("OK", 0, 1, 3))
        self.assertEqual([self.status(i) for i in (1, 2, 3, 4)], ["CANARY", "CANARY", "CANARY", None])     # released, asked after the reset
        five = counts["cloud_quota"]["windows"]["5h"]
        self.assertEqual((five["used_first"], five["used_last"], five["estimate"], five["per_call_percent"], five["calls_left"]), (47, 50, "ESTIMATED", 1.0, 0))
        self.assertEqual(five["resets"], "Oct 20 02:12 AM CDT")                                             # Central time, the way Jim reads it
        week = counts["cloud_quota"]["windows"]["Week"]
        self.assertEqual((week["estimate"], week["per_call_below"], week["calls_left_at_least"]), ("BELOW_RESOLUTION", 0.3333, 147))

    def test_no_policy_or_an_unreadable_meter_is_a_named_failure_and_nothing_is_lost(self):
        for reason in ("QUOTA_UNCONFIGURED", "METER_UNAVAILABLE"):
            self.setUp()
            self.titled(1, "Program Manager 1")
            counts = self.batch(5, Endpoint(lambda data: refusal(data, reason)))
            self.assertEqual((counts["cloud_fit"], counts["failed"], self.status(1)), (f"DEGRADED(FIT_EDGE_REFUSED_{reason})", 1, None))
            self.assertNotIn("cloud_quota", counts)

    def test_a_run_without_meter_readings_reports_no_estimate(self):
        self.titled(1, "Program Manager 1")
        self.assertNotIn("cloud_quota", self.batch(5, Endpoint(measured)))                                   # an Oracle without the meter: nothing invented


class QuotaReading(Batch):
    def test_only_labels_and_numbers_are_kept_and_anything_off_contract_is_none(self):
        raw = {**metered(12), "account": "someone", "plan": "plus"}
        raw["windows"][0]["profile"] = "openai:meter"
        self.assertEqual(edge.quota_reading(raw), metered(12))
        for bad in (None, {}, {"stop": 0, "windows": metered(1)["windows"]}, {"stop": 50, "windows": []}, {"stop": "50", "windows": metered(1)["windows"]},
                    {"stop": 50, "windows": [{"label": "5h", "used": 101, "reset_at": "x"}]}, {"stop": 50, "windows": [{"label": "5h", "used": "1", "reset_at": "x"}]},
                    {"stop": 50, "windows": [{"label": "", "used": 1, "reset_at": "x"}]}):
            self.assertIsNone(edge.quota_reading(bad), bad)

    def test_a_reset_between_readings_starts_a_new_segment(self):
        summary = cloud.quota_summary([metered(40), metered(45), metered(1), metered(3)])
        five = summary["windows"]["5h"]
        self.assertEqual((five["basis_calls"], five["per_call_percent"], five["calls_left"]), (2, 3.5, 13))
        self.assertIsNone(cloud.quota_summary([None, None]))


class ReceiptFieldIsNamed(Batch):
    def test_a_rejected_receipt_names_its_field_so_a_stop_is_never_a_guess(self):
        """LIFE-77 batch 1 (Oct 19): 45 good calls, then FIT_EDGE_RECEIPT_REJECTED with nothing to say which field. The fixed field name is now counted."""
        self.titled(1, "Program Manager A")
        counts = self.batch(5, Endpoint(lambda data: reply(data, receipt={**RECEIPT, "usage": USAGE, "run_finished": False}, judgment_raw=json.dumps({"score": 90, "rationale": "x", "confidence": "high"}))))
        self.assertEqual((counts["cloud_fit"], counts["cloud_receipt_fields"]), ("DEGRADED(FIT_EDGE_RECEIPT_REJECTED)", {"RUN_FINISHED": 1}))


class BacklogIsReportedToOracle(Batch):
    """LIFE-77: the AI Capacity card's proposed-usage line needs the backlog count, which only V7's store knows; the batch reports it to Oracle at its end."""

    def test_a_live_batch_reports_what_is_left_and_a_dry_run_reports_nothing(self):
        for i in (1, 2, 3):
            self.titled(i, f"Program Manager {i}", score=90 - i)
        counts = self.batch(1, Endpoint(measured))
        self.assertEqual(self.reported, ("oracle.example", sum(counts["remaining_after"].values())))
        self.assertEqual(counts["backlog_reported"], 2)
        self.reported = None
        self.batch(1, Endpoint(measured), live=False)
        self.assertIsNone(self.reported)

    def test_a_failed_report_never_fails_the_batch(self):
        self.titled(1, "Program Manager 1")
        self.report_fails = "FIT_EDGE_NO_REPLY"
        counts = self.batch(1, Endpoint(measured))
        self.assertEqual((counts["cloud_fit"], counts["failed"], counts["backlog_report"]), ("OK", 0, "FIT_EDGE_NO_REPLY"))



class ParallelLanes(Batch):
    """D232 (parallel Luna, Jim Oct 20): a batch keeps up to V7_CLOUD_FIT_PARALLEL calls in flight, one per Oracle lane. Only the calls run at once: every claim,
    write, promotion and count stays on the batch's own thread, each job is asked once, and a stop lets the calls already sent finish and be recorded."""
    TWO = {**ENV, "V7_CLOUD_FIT_PARALLEL": "2"}

    def test_two_calls_are_in_flight_at_once_and_every_job_is_asked_once_and_promoted(self):
        for i in range(1, 6):
            self.titled(i, f"Program Manager {i}", score=90 - i)
        both = threading.Barrier(2, timeout=5)                                                  # the first two calls must be on Oracle together, or this times out
        arrivals, lock = [], threading.Lock()

        def answer(data):
            with lock:
                arrivals.append(data)
                n = len(arrivals)
            if n <= 2:
                both.wait()
            return reply(data, receipt={**RECEIPT, "usage": USAGE, "agent": "fit2" if n % 2 == 0 else "fit"},     # each lane names its own agent
                         judgment_raw=json.dumps({"score": 90, "rationale": "Aligned on delivery ownership.", "confidence": "high"}))
        endpoint = Endpoint(answer)
        counts = self.batch(10, endpoint, environ=self.TWO)
        self.assertEqual(sorted(self.asked(endpoint)), [f"Program Manager {i}" for i in range(1, 6)])
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], counts["cloud_ok"], counts["cloud_promoted"], counts["failed"]), ("OK", 5, 5, 5, 0))
        self.assertEqual([self.status(i) for i in range(1, 6)], ["CANARY"] * 5)
        self.assertEqual(counts["cloud_tokens_total"], 5 * USAGE["total_tokens"])

    def test_busy_from_a_lane_oracle_does_not_have_releases_that_job_and_the_batch_continues_with_fewer_calls(self):
        for i in range(1, 6):
            self.titled(i, f"Program Manager {i}", score=90 - i)
        active, lock = [0], threading.Lock()

        def one_lane(data):
            with lock:
                if active[0]:
                    return refusal(data, status="BUSY")                                         # Oracle has one lane open: no model runs for a second call
                active[0] += 1
            time.sleep(0.5)
            with lock:
                active[0] -= 1
            return measured(data)
        counts = self.batch(10, Endpoint(one_lane), environ={**ENV, "V7_CLOUD_FIT_PARALLEL": "3"})
        statuses = [self.status(i) for i in range(1, 6)]
        self.assertEqual((counts["cloud_fit"], counts["failed"], counts["cloud_busy"], counts["cloud_promoted"]), ("OK", 0, 2, 3))
        self.assertEqual((statuses.count("CANARY"), statuses.count(None)), (3, 2))                # the two refused jobs were released: asked by the next batch

    def test_a_stop_while_another_call_is_in_flight_records_that_answer_and_sends_nothing_more(self):
        for i in range(1, 5):
            self.titled(i, f"Program Manager {i}", score=90 - i)
        refused, arrivals, lock = threading.Event(), [], threading.Lock()

        def answer(data):
            with lock:
                arrivals.append(data)
                n = len(arrivals)
            if n == 1:
                refused.wait(5)
                time.sleep(0.3)                                                                 # still running on Oracle after the other call was refused
                return measured(data)
            refused.set()
            return reply(data, status="REFUSED", reason="QUOTA_RESERVE", judgment_raw=None, receipt=None, quota=metered(50))
        endpoint = Endpoint(answer)
        counts = self.batch(10, endpoint, environ=self.TWO)
        self.assertEqual((len(endpoint.sent), counts["cloud_calls"], counts["cloud_quota_reserve"], counts["cloud_promoted"], counts["cloud_fit"]), (2, 2, 1, 1, "OK"))
        statuses = [self.status(i) for i in range(1, 5)]
        self.assertEqual((statuses.count("CANARY"), statuses.count(None)), (1, 3))                # the in-flight answer kept; the refused job and the rest wait

    def test_a_job_failure_in_one_lane_is_recorded_while_the_other_lanes_answer_is_kept(self):
        for i in range(1, 4):
            self.titled(i, f"Program Manager {i}", score=90 - i)
        arrivals, lock = [], threading.Lock()

        def answer(data):
            with lock:
                arrivals.append(data)
                n = len(arrivals)
            if n == 1:
                return reply(data, status="TIMEOUT", judgment_raw=None)                         # a model may have run: recorded, never re-asked, and the batch stops
            time.sleep(0.3)
            return measured(data)
        counts = self.batch(10, Endpoint(answer), environ=self.TWO)
        statuses = [self.status(i) for i in range(1, 4)]
        self.assertEqual((counts["cloud_fit"], counts["cloud_failed"], counts["cloud_promoted"]), ("DEGRADED(FIT_EDGE_TIMEOUT)", 1, 1))
        self.assertEqual(sorted(s or "-" for s in statuses), ["-", "CANARY", "DEGRADED"])

    def test_the_setting_is_one_unless_it_is_a_whole_number_from_1_to_3(self):
        for raw, want in ((None, 1), ("", 1), ("1", 1), ("2", 2), (" 3 ", 3), ("4", 1), ("0", 1), ("-2", 1), ("two", 1), ("2.5", 1)):
            self.assertEqual(cloud.parallel({} if raw is None else {"V7_CLOUD_FIT_PARALLEL": raw}), want, raw)
