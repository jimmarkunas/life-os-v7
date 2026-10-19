"""D214 (LIFE-41 10472 C, 10474, 10475): the cloud-Fit shadow. Production SQL over the sqlite kit, the production request builder, edge.invoke and judge.accept; only the Oracle endpoint is
a fake that follows its wire contract. Nothing here calls a model or the network, and the switch is never set in any workflow (test_edge.Dormant)."""
import datetime
import json
import unittest
from unittest import mock

from lifeos.jobs.fit import cloud, edge, stage
from tests.jobs.fit.test_edge import Endpoint, refusal, reply
from tests.jobs.fit.test_fit import GOOD, PROFILE

T = datetime.datetime(2026, 10, 18, 22, 0, 0)
REF = "0123456789abcdef"
SHADOW = {"V7_CLOUD_FIT": "shadow", "V7_CLOUD_FIT_PROFILE_REF": REF, "IMESSAGE_ALERTS_HOST": "oracle.example"}
STATIC = ("score", "decision", "line", "why", "exclusion", "confidence", "model_version", "profile_hash", "jd_fingerprint", "admission", "admission_reason", "lane", "eligible")
CLOUD = ("cloud_status", "cloud_reason", "cloud_score", "cloud_confidence", "cloud_rationale", "cloud_request_hash", "cloud_model", "cloud_run_id", "cloud_jd_fingerprint", "cloud_at")


class Base(unittest.TestCase):
    def setUp(self):
        from tests.kit.jobsdb import JobsDB
        self.db = JobsDB()
        self.addCleanup(self.db.raw.close)

    def job(self, job_id, status="READY", admission="ADMIT", page=None, exclusion=None, cloud_status=None, seen=T, text=GOOD, kind="ats_api"):
        self.db.insert("v7_jobs", id=job_id, dedupe_key=f"k{job_id:03d}", status=status, title="Technical Program Manager", company="Acme", first_seen=seen, last_seen=seen,
                       updated_at=seen, lane="Newsletter", location_text="Remote - United States", notion_page_id=page)
        self.db.insert("v7_job_descriptions", job_id=job_id, source_kind=kind, full_text=text, fingerprint=f"fp{job_id}", fetched_at=seen)
        self.db.insert("v7_job_fit", job_id=job_id, score=80, decision="Go", line="fits", confidence="high", model_version="6", profile_hash="p", jd_fingerprint=f"fp{job_id}",
                       scored_at=seen, admission=admission, admission_reason=None, lane="US Remote", eligible="US Remote", exclusion=exclusion, cloud_status=cloud_status)

    def row(self, job_id, columns):
        return self.db.raw.execute(f"SELECT {', '.join(columns)} FROM v7_job_fit WHERE job_id=?", (job_id,)).fetchone()

    def run_cloud(self, environ, send, live=True, clock=None, started=None):
        with mock.patch.object(cloud.store, "connect", return_value=self.db):
            return cloud.run(environ, live, started, PROFILE, send=send, **({"clock": clock} if clock else {}))


class OffByDefault(Base):
    def test_unset_unknown_and_off_read_nothing_and_call_nothing(self):
        self.job(1)
        endpoint = Endpoint()
        for environ in ({}, {"V7_CLOUD_FIT": "off"}, {"V7_CLOUD_FIT": "yes"}, {"V7_CLOUD_FIT": ""}, {**SHADOW, "V7_CLOUD_FIT": "OFF"}):
            with mock.patch.object(cloud.store, "connect", side_effect=AssertionError("off must not touch the store")):
                counts = cloud.run(environ, True, send=endpoint)
            self.assertEqual((counts["cloud_fit"], counts["cloud_calls"]), ("off", 0), environ)
        self.assertEqual(endpoint.sent, [])
        self.assertEqual(self.row(1, CLOUD), (None,) * len(CLOUD))

    def test_canary_and_on_are_refused_with_no_call(self):
        self.job(1)
        endpoint = Endpoint()
        for value in ("canary", "on"):
            counts = self.run_cloud({**SHADOW, "V7_CLOUD_FIT": value}, endpoint)
            self.assertEqual((counts["cloud_fit"], counts["cloud_calls"]), ("DEGRADED(MODE_NOT_AUTHORIZED)", 0))
        self.assertEqual(endpoint.sent, [])

    def test_a_dry_run_and_missing_configuration_ask_nothing(self):
        self.job(1)
        endpoint = Endpoint()
        self.assertEqual(self.run_cloud(SHADOW, endpoint, live=False)["cloud_fit"], "dry_run")
        for environ, code in (({**SHADOW, "V7_CLOUD_FIT_PROFILE_REF": ""}, "PROFILE_REF_UNCONFIGURED"), ({**SHADOW, "V7_CLOUD_FIT_PROFILE_REF": "Jim Markunas, 15 years"}, "PROFILE_REF_UNCONFIGURED"),
                              ({**SHADOW, "IMESSAGE_ALERTS_HOST": ""}, "FIT_EDGE_NO_HOST"), ({**SHADOW, "V7_CLOUD_FIT_PER_RUN": "0"}, "PER_RUN_ZERO")):
            counts = self.run_cloud(environ, endpoint)
            self.assertEqual((counts["cloud_fit"], counts["cloud_calls"]), (f"DEGRADED({code})", 0))
        self.assertEqual(endpoint.sent, [])

    def test_the_fit_stage_composes_it_after_its_static_write_and_off_leaves_the_stage_counts_unchanged(self):
        self.job(1)
        self.db.raw.execute("DELETE FROM v7_job_fit")                                          # a new job: the stage scores it
        endpoint = Endpoint()
        with mock.patch.object(stage.store, "connect", return_value=self.db), mock.patch.object(stage.store, "ensure_schema"), \
                mock.patch.object(stage.fit_profile, "load", return_value=PROFILE), mock.patch.object(stage.sponsors, "load", return_value=None), \
                mock.patch.object(edge, "exchange", endpoint):
            counts = stage.run(10, True, {})
        self.assertEqual((counts["picked"], counts["cloud_fit"], counts["cloud_calls"]), (1, "off", 0))
        self.assertEqual(endpoint.sent, [])
        self.assertIsNotNone(self.row(1, ("score",))[0])


class Selection(Base):
    def test_only_new_eligible_unasked_records_are_asked_in_a_stable_order(self):
        self.job(1, status="PUBLISHED", page="page1")                                          # published: never, whatever changes on it later
        self.job(2, page="page2")                                                              # has a Ledger page
        self.job(3, admission="EXCLUDE")
        self.job(4, exclusion="named employer")
        self.job(5, cloud_status="OK")                                                         # already asked
        self.job(6, status="EXCLUDED_FIT")
        self.job(8, seen=T + datetime.timedelta(hours=1))
        self.job(7, status="RESOLVED", admission="REVIEW")
        self.job(9, kind="title_only")                                                         # LIFE-41 10500: not a public employer / ATS posting
        self.job(10, kind="title_watch")
        asked = []
        for _ in range(4):                                                                     # one a tick, oldest first, then nothing left
            counts = self.run_cloud({**SHADOW, "V7_CLOUD_FIT_PER_RUN": "2"}, Endpoint())
            asked.append((counts["cloud_fit"], counts["cloud_calls"]))
        self.assertEqual(asked, [("OK", 1), ("OK", 1), ("OK", 0), ("OK", 0)])
        done = [r[0] for r in self.db.raw.execute("SELECT job_id FROM v7_job_fit WHERE cloud_status IS NOT NULL AND job_id <> 5 ORDER BY job_id")]
        self.assertEqual(done, [7, 8])                                                         # 7 (RESOLVED, first seen T) then 8; never 1-4, 6, 9 or 10

    def test_a_published_job_whose_description_changed_is_still_never_asked(self):
        self.job(1, status="PUBLISHED", page="page1")
        self.db.raw.execute("UPDATE v7_job_descriptions SET fingerprint='changed' WHERE job_id=1")
        self.db.raw.execute("UPDATE v7_job_fit SET jd_fingerprint='changed', profile_hash='new' WHERE job_id=1")
        endpoint = Endpoint()
        self.assertEqual(self.run_cloud(SHADOW, endpoint)["cloud_calls"], 0)
        self.assertEqual(endpoint.sent, [])

    def test_at_most_one_cloud_call_a_tick_whatever_the_variable_says(self):
        for i in range(1, 6):
            self.job(i)
        for value in (None, "1", "2", "50"):
            endpoint = Endpoint()
            self.assertEqual(self.run_cloud({**SHADOW, **({"V7_CLOUD_FIT_PER_RUN": value} if value else {})}, endpoint)["cloud_calls"], 1, value)
            self.assertEqual(len(endpoint.sent), 1, value)
        self.assertEqual(cloud.MAX_PER_RUN, 1)
        self.assertEqual((cloud.per_run({}), cloud.per_run({"V7_CLOUD_FIT_PER_RUN": "x"}), cloud.per_run({"V7_CLOUD_FIT_PER_RUN": "-3"})), (1, 1, 0))


class ShadowNeverChangesWhatIsVisible(Base):
    def test_the_static_fit_admission_and_job_row_are_untouched_and_the_answer_is_stored_privately(self):
        self.job(1)
        before_fit, before_job = self.row(1, STATIC), self.db.raw.execute("SELECT * FROM v7_jobs WHERE id=1").fetchone()
        endpoint = Endpoint()
        counts = self.run_cloud(SHADOW, endpoint)
        self.assertEqual((counts["cloud_fit"], counts["cloud_ok"]), ("OK", 1))
        self.assertEqual(self.row(1, STATIC), before_fit)
        self.assertEqual(self.db.raw.execute("SELECT * FROM v7_jobs WHERE id=1").fetchone(), before_job)
        status, reason, score, confidence, rationale, request_hash, model, run_id, fingerprint, at = self.row(1, CLOUD)
        self.assertEqual((status, reason, score, confidence, model, run_id, fingerprint), ("OK", None, 90, "high", "gpt-6-luna", "run-1234567890", "fp1"))
        self.assertTrue(rationale and at and len(request_hash) == 16)
        sent = json.loads(endpoint.sent[0].split(b"\n", 1)[1])["request"]
        self.assertEqual(sent["profile"], {"ref": REF})                                        # V7 sends a reference to Oracle's brief, never profile text

    def test_the_counts_line_carries_no_identifier_or_model_text(self):
        self.job(1)
        counts = self.run_cloud(SHADOW, Endpoint())
        text = json.dumps(counts)
        for private in ("k001", "Aligned on delivery", "run-1234567890", REF, "oracle.example", "Acme"):
            self.assertNotIn(private, text)
        self.job(2, seen=T + datetime.timedelta(hours=1))
        failed = json.dumps(self.run_cloud(SHADOW, Endpoint(lambda data: reply(data, receipt={**json.loads(reply(data))["receipt"], "fallback": True}))))
        self.assertIn("DEGRADED(FIT_EDGE_RECEIPT_REJECTED)", failed)
        for private in ("k002", "fallback", "Run delivery", "Technical Program Manager", "8+ years", REF):      # no field name, JD text, title or reference
            self.assertNotIn(private, failed)


class FailuresAreVisiblyDegraded(Base):
    def test_a_refusal_writes_nothing_and_stops_further_calls(self):
        self.job(1)
        self.job(2)
        for reason in ("LIMIT_UNCONFIGURED", "DAILY_LIMIT", "REAL_DISABLED", "NOT_ALLOWLISTED", "PURGE_FAILED"):
            endpoint = Endpoint(lambda data, reason=reason: refusal(data, reason))
            counts = self.run_cloud(SHADOW, endpoint)
            self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], len(endpoint.sent)), (f"DEGRADED(FIT_EDGE_REFUSED_{reason})", 1, 1))
            self.assertEqual([self.row(i, ("cloud_status",))[0] for i in (1, 2)], [None, None])     # asked again on a later tick, never shown as a cloud answer

    def test_transport_and_receipt_failures_are_named_codes(self):
        self.job(1)
        cases = ((lambda data: b"", "FIT_EDGE_NO_REPLY"), (lambda data: reply(data, status="BUSY"), "FIT_EDGE_BUSY"),
                 (lambda data: reply(data, request_hash="0" * 64), "FIT_EDGE_HASH_MISMATCH"),
                 (lambda data: reply(data, receipt={**json.loads(reply(data))["receipt"], "fallback": True}), "FIT_EDGE_RECEIPT_REJECTED"),
                 (lambda data: reply(data, status="MODEL_DRIFT"), "FIT_EDGE_MODEL_DRIFT"))
        for answer, code in cases:
            counts = self.run_cloud(SHADOW, Endpoint(answer))
            self.assertEqual(counts["cloud_fit"], f"DEGRADED({code})")
            self.assertIsNone(self.row(1, ("cloud_status",))[0])

    def test_an_unusable_judgment_is_recorded_on_its_job_never_as_a_score_and_the_next_tick_moves_on(self):
        self.job(1)
        self.job(2, seen=T + datetime.timedelta(hours=1))
        counts = self.run_cloud(SHADOW, Endpoint(lambda data: reply(data, judgment_raw="not json")))
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], counts["cloud_ok"]), ("DEGRADED(FIT_EDGE_JUDGMENT_REJECTED)", 1, 0))
        self.assertEqual(self.row(1, ("cloud_status", "cloud_reason", "cloud_score")), ("DEGRADED", "FIT_EDGE_JUDGMENT_REJECTED", None))
        self.assertEqual(self.run_cloud(SHADOW, Endpoint())["cloud_fit"], "OK")
        self.assertEqual(self.row(2, ("cloud_status",))[0], "OK")

    def test_nothing_substantive_to_send_is_skipped_without_a_call(self):
        self.job(1, text="Short.")
        endpoint = Endpoint()
        counts = self.run_cloud(SHADOW, endpoint)
        self.assertEqual((counts["cloud_skipped"], counts["cloud_calls"], endpoint.sent), (1, 0, []))
        self.assertEqual(self.row(1, ("cloud_status", "cloud_reason")), ("SKIPPED", "NO_SUBSTANTIVE_TEXT"))

    def test_a_call_starts_only_when_the_fit_stage_budget_can_hold_it(self):
        self.job(1)
        endpoint = Endpoint()
        counts = self.run_cloud(SHADOW, endpoint, clock=lambda: 1000.0, started=1000.0 - (cloud.STAGE_BUDGET - cloud.CALL_SECONDS) - 1)
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], endpoint.sent), ("DEGRADED(STAGE_BUDGET)", 0, []))
        from lifeos.jobs import tick
        self.assertEqual(next(s.budget for s in tick.PLAN if s.name == "fit"), cloud.STAGE_BUDGET)
        self.assertLessEqual(cloud.MAX_PER_RUN * cloud.CALL_SECONDS, cloud.STAGE_BUDGET)

    def test_oracle_refusal_reasons_are_recognised(self):
        for reason in ("LIMIT_UNCONFIGURED", "DAILY_LIMIT", "REAL_DISABLED", "BRIEF_MISSING", "BRIEF_UNSAFE", "BRIEF_HASH_MISMATCH", "BRIEF_INVALID", "PROFILE_MISMATCH", "PURGE_FAILED"):
            self.assertIn(reason, edge.REASONS)


class CapsApplyToTheCloudScore(Base):
    def test_a_hard_family_title_is_capped_at_40_and_a_junior_title_at_the_junior_cap(self):
        from lifeos.jobs.fit.score import JUNIOR_CAP
        for title, cap in (("Senior Product Marketing Manager", 40), ("Program Analyst", JUNIOR_CAP)):
            self.setUp()
            self.job(1)
            self.db.raw.execute("UPDATE v7_jobs SET title=? WHERE id=1", (title,))
            self.assertEqual(self.run_cloud(SHADOW, Endpoint())["cloud_ok"], 1, title)          # the fake model answers 90
            self.assertEqual(self.row(1, ("cloud_score",))[0], cap, title)

    def test_without_the_stage_profile_nothing_is_asked(self):
        self.job(1)
        endpoint = Endpoint()
        with mock.patch.object(cloud.store, "connect", return_value=self.db):
            counts = cloud.run(SHADOW, True, send=endpoint)
        self.assertEqual((counts["cloud_fit"], endpoint.sent), ("DEGRADED(PROFILE_MISSING)", []))


class StaticWritePreservesCloudColumns(Base):
    def _static(self, job_id, score):
        values = dict(job_id=job_id, score=score, decision="Go", line="new line", why=None, exclusion=None, confidence="high", buckets="{}", trace="[]", model_version="6",
                      profile_hash="p2", jd_fingerprint="fp-new", scored_at=T, shadow_score=None, shadow_changes=0, admission="ADMIT", admission_reason=None, work_mode="remote",
                      lane="US Remote", eligible="US Remote")
        return tuple(values[c] for c in stage._STATIC)

    def test_a_re_score_keeps_the_cloud_result_and_a_new_job_gets_a_row(self):
        self.job(1, cloud_status="OK")
        self.db.raw.execute("UPDATE v7_job_fit SET cloud_score=88, cloud_rationale='private' WHERE job_id=1")
        cursor = self.db.cursor()
        cursor.execute(stage.WRITE, self._static(1, 65))
        cursor.execute(stage.WRITE, self._static(2, 70))
        self.assertEqual(self.row(1, ("score", "line", "cloud_status", "cloud_score", "cloud_rationale")), (65, "new line", "OK", 88, "private"))
        self.assertEqual(self.row(2, ("score", "cloud_status")), (70, None))

    def test_the_replaced_statement_was_the_hazard(self):
        """Negative control: the old REPLACE INTO deletes the row first, so the cloud columns it does not name are lost."""
        self.job(1, cloud_status="OK")
        old = "REPLACE INTO v7_job_fit (" + ", ".join(stage._STATIC) + ") VALUES (" + ",".join(["%s"] * len(stage._STATIC)) + ")"
        self.db.cursor().execute(old, self._static(1, 65))
        self.assertIsNone(self.row(1, ("cloud_status",))[0])
        self.assertNotIn("\"REPLACE INTO", open(stage.__file__).read())                       # no statement uses it any more
        self.assertTrue(stage.WRITE.startswith("INSERT INTO v7_job_fit"))

    def test_rejudge_marks_stale_and_keeps_the_cloud_result(self):
        self.job(1, admission="EXCLUDE", cloud_status="OK")
        self.assertEqual(stage.rejudge_excluded(self.db.cursor()), 1)
        self.assertEqual(self.row(1, ("model_version", "cloud_status")), ("0", "OK"))


if __name__ == "__main__":
    unittest.main()
