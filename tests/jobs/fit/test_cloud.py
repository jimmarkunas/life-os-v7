"""D214 (LIFE-41 10472 C, 10474, 10475): the cloud-Fit shadow. Production SQL over the sqlite kit, the production request builder, edge.invoke and judge.accept; only the Oracle endpoint is
a fake that follows its wire contract. Nothing here calls a model or the network, and the switch is never set in any workflow (test_edge.Dormant)."""
import datetime
import json
import unittest
from unittest import mock

from lifeos import run as lifeos_run
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

    def job(self, job_id, status="READY", admission="ADMIT", page=None, exclusion=None, cloud_status=None, seen=T, text=GOOD, kind="ats_api",
            url="https://boards.greenhouse.io/acme/jobs/100", source="jobright"):
        self.db.insert("v7_jobs", id=job_id, dedupe_key=f"k{job_id:03d}", status=status, title="Technical Program Manager", company="Acme", first_seen=seen, last_seen=seen,
                       updated_at=seen, lane="Newsletter", location_text="Remote - United States", notion_page_id=page, final_apply_url=url, source=source)
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

    def test_on_is_refused_and_canary_outside_the_fit_stage_asks_nothing(self):
        self.job(1)
        endpoint = Endpoint()
        self.assertEqual(self.run_cloud({**SHADOW, "V7_CLOUD_FIT": "on"}, endpoint)["cloud_fit"], "DEGRADED(MODE_NOT_AUTHORIZED)")      # G8 full on: a separate decision
        self.assertEqual(self.run_cloud({**SHADOW, "V7_CLOUD_FIT": "canary"}, endpoint)["cloud_fit"], "DEGRADED(CANARY_WITHOUT_PROMOTION)")   # admission is the stage's
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
        self.assertEqual((cloud.per_run({}), cloud.per_run({"V7_CLOUD_FIT_PER_RUN": "x"}), cloud.per_run({"V7_CLOUD_FIT_PER_RUN": "-3"})), (1, 0, 0))     # an invalid explicit value fails closed


class ScanPastSkips(Base):
    """LIFE-41 G6 (proof run 39202038517 picked one candidate, skipped it and made no call): skips cost no network, so a run examines up to SCAN candidates and
    asks the first sendable one. Still at most one call; later candidates stay unasked and untouched; the counts say why rows were skipped, never which."""

    def unsendable(self):
        self.job(1, url="https://www.dice.com/job-detail/abc", source="dice")                 # NOT_PUBLIC_POSTING
        self.job(2, seen=T + datetime.timedelta(minutes=1))
        self.db.raw.execute("UPDATE v7_jobs SET title=? WHERE id=2", ("Senior Technical Program Manager London",))   # TITLE_LOCATION
        self.job(3, seen=T + datetime.timedelta(minutes=2), text="Short.")                     # NO_SUBSTANTIVE_TEXT

    def test_unsendable_oldest_candidates_do_not_use_up_the_one_call(self):
        self.unsendable()
        self.job(4, seen=T + datetime.timedelta(minutes=3))
        self.job(5, seen=T + datetime.timedelta(minutes=4))
        endpoint = Endpoint()
        counts = self.run_cloud(SHADOW, endpoint)
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], counts["cloud_ok"], counts["cloud_skipped"], len(endpoint.sent)), ("OK", 1, 1, 3, 1))
        self.assertEqual(counts["cloud_skip_reasons"], {"NOT_PUBLIC_POSTING": 1, "TITLE_LOCATION": 1, "NO_SUBSTANTIVE_TEXT": 1})
        self.assertEqual([self.row(i, ("cloud_status", "cloud_reason")) for i in (1, 2, 3, 4)],
                         [("SKIPPED", "NOT_PUBLIC_POSTING"), ("SKIPPED", "TITLE_LOCATION"), ("SKIPPED", "NO_SUBSTANTIVE_TEXT"), ("OK", None)])
        self.assertEqual(self.row(5, CLOUD), (None,) * len(CLOUD))                             # after the one call: never examined, never written
        again = Endpoint()
        self.assertEqual((self.run_cloud(SHADOW, again)["cloud_calls"], len(again.sent)), (1, 1))     # the next tick asks job 5, and only it
        self.assertEqual(self.row(5, ("cloud_status",))[0], "OK")

    def test_the_scan_is_bounded_and_resumes_on_the_next_run(self):
        for i in range(1, cloud.SCAN + 3):
            self.job(i, seen=T + datetime.timedelta(minutes=i), url="https://www.lensa.com/job/1", source="lensa")
        self.job(cloud.SCAN + 3, seen=T + datetime.timedelta(minutes=cloud.SCAN + 3))
        endpoint = Endpoint()
        first = self.run_cloud(SHADOW, endpoint)
        self.assertEqual((first["cloud_calls"], first["cloud_skipped"], first["cloud_skip_reasons"], endpoint.sent), (0, cloud.SCAN, {"NOT_PUBLIC_POSTING": cloud.SCAN}, []))
        second = self.run_cloud(SHADOW, endpoint)
        self.assertEqual((second["cloud_calls"], second["cloud_skipped"], len(endpoint.sent)), (1, 2, 1))
        self.assertEqual(self.row(cloud.SCAN + 3, ("cloud_status",))[0], "OK")

    def test_a_refusal_after_skips_keeps_the_skips_writes_nothing_for_the_asked_job_and_stops(self):
        self.unsendable()
        self.job(4, seen=T + datetime.timedelta(minutes=3))
        self.job(5, seen=T + datetime.timedelta(minutes=4))
        endpoint = Endpoint(lambda data: refusal(data, "DAILY_LIMIT"))
        counts = self.run_cloud(SHADOW, endpoint)
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], counts["cloud_skipped"], len(endpoint.sent)), ("DEGRADED(FIT_EDGE_REFUSED_DAILY_LIMIT)", 1, 3, 1))
        self.assertEqual([self.row(i, ("cloud_status",))[0] for i in (1, 2, 3, 4, 5)], ["SKIPPED", "SKIPPED", "SKIPPED", None, None])

    def test_off_and_unconfigured_runs_still_examine_nothing(self):
        self.unsendable()
        for environ in ({}, {**SHADOW, "V7_CLOUD_FIT_PROFILE_REF": ""}, {**SHADOW, "V7_CLOUD_FIT_PER_RUN": "0"}):
            counts = self.run_cloud(environ, Endpoint())
            self.assertEqual((counts["cloud_skipped"], counts["cloud_skip_reasons"]), (0, {}))
        self.assertEqual([self.row(i, ("cloud_status",))[0] for i in (1, 2, 3)], [None, None, None])


class AuditGuardBeforeEgress(Base):
    """LIFE-41 G6 (run 39219054103 spent its only call on a row the same tick's audit then demoted with audit_url_listing_url): the precision audit's own
    verdict (lifeos.jobs.audit.judge, same inputs) is the last gate before egress, so a listing, root or no-job-id link never consumes the call."""
    BAD = (("https://boards.greenhouse.io/acme/jobs/search?q=program+manager", "AUDIT_URL_LISTING_URL"),
           ("https://boards.greenhouse.io/", "AUDIT_URL_ROOT"),
           ("https://jobs.lever.co/acme/", "AUDIT_URL_NO_JOB_ID"))

    def test_audit_failing_links_are_skipped_and_the_first_eligible_candidate_gets_the_one_call_and_persists(self):
        for i, (url, _) in enumerate(self.BAD, 1):
            self.job(i, seen=T + datetime.timedelta(minutes=i), url=url)
        self.job(4, seen=T + datetime.timedelta(minutes=4))
        self.job(5, seen=T + datetime.timedelta(minutes=5))
        endpoint = Endpoint()
        counts = self.run_cloud(SHADOW, endpoint)
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], counts["cloud_ok"], counts["cloud_skipped"], len(endpoint.sent)), ("OK", 1, 1, 3, 1))
        self.assertEqual(counts["cloud_skip_reasons"], {code: 1 for _, code in self.BAD})
        for i, (_, code) in enumerate(self.BAD, 1):
            self.assertEqual(self.row(i, ("cloud_status", "cloud_reason", "cloud_score")), ("SKIPPED", code, None))
        status, score, confidence, model, run_id, request_hash = self.row(4, ("cloud_status", "cloud_score", "cloud_confidence", "cloud_model", "cloud_run_id", "cloud_request_hash"))
        self.assertEqual(status, "OK")                                                          # the valid Oracle result persists privately
        self.assertIsInstance(score, int)
        self.assertTrue(confidence and model and run_id and request_hash)
        self.assertEqual(self.row(5, CLOUD), (None,) * len(CLOUD))                             # after the one call: untouched

    def test_the_verdict_is_exactly_the_audits_including_its_title_proof_exemption(self):
        from lifeos.jobs import audit
        for url, code in self.BAD:
            self.assertEqual(cloud.audit_problem(url, GOOD, None, "jobright", "Acme", "Technical Program Manager"), code)
            self.assertEqual(code, audit.judge(url, GOOD).upper())
        ambiguous = "https://jobs.lever.co/acme/"                                               # no_job_id is ambiguous: a page proven by title is kept by the audit
        self.assertIsNone(audit.judge(ambiguous, GOOD, proven=True))
        self.assertIsNone(cloud.audit_problem(ambiguous, GOOD, "title", "jobright", "Acme", "Technical Program Manager"))
        self.job(1, url=ambiguous)
        self.db.raw.execute("UPDATE v7_jobs SET link_proof='title' WHERE id=1")
        endpoint = Endpoint()
        self.assertEqual((self.run_cloud(SHADOW, endpoint)["cloud_ok"], len(endpoint.sent)), (1, 1))     # so the shadow sends it too
        self.assertEqual(cloud.audit_problem("https://boards.greenhouse.io/", GOOD, "title", "jobright", "Acme", "Technical Program Manager"), "AUDIT_URL_ROOT")  # a root never qualifies

    def test_an_audit_skip_writes_only_the_private_cloud_columns(self):
        self.job(1, url=self.BAD[0][0])
        before = self.row(1, STATIC)
        job_before = self.db.raw.execute("SELECT status, final_apply_url, notion_page_id FROM v7_jobs WHERE id=1").fetchone()
        self.run_cloud(SHADOW, Endpoint())
        self.assertEqual(self.row(1, STATIC), before)                                          # static Fit and admission unchanged; the audit itself still decides demotion
        self.assertEqual(self.db.raw.execute("SELECT status, final_apply_url, notion_page_id FROM v7_jobs WHERE id=1").fetchone(), job_before)


class CloudFitCanary(Base):
    """D218 (Jim, LIFE-41 10555): canary. The one job a tick the model judges gets the accepted cloud score as its visible Fit, decided by the Fit stage's own lane
    rules and gate, provenance 'Cloud Fit ...' in Why It Fits; the static scorer never overwrites it; every failure leaves the visible Fit exactly as the static one."""
    CANARY = {**SHADOW, "V7_CLOUD_FIT": "canary", "V7_FIT_GATE": "true"}

    def stage_run(self, environ, endpoint):
        with mock.patch.object(stage.store, "connect", return_value=self.db), mock.patch.object(stage.store, "ensure_schema"), \
                mock.patch.object(stage.fit_profile, "load", return_value=PROFILE), mock.patch.object(stage.sponsors, "load", return_value=None), \
                mock.patch.object(edge, "exchange", endpoint):
            return stage.run(10, True, environ)

    def visible(self, job_id):
        return self.row(job_id, ("score", "decision", "line", "admission", "cloud_status"))

    def published_fit(self, job_id):
        from lifeos.jobs import publish
        with mock.patch.object(publish, "_now", return_value=T):
            rows, _ = publish._pick(self.db, 10, gated=True)
        row = next((r for r in rows if r[0] == job_id), None)
        if row is None:
            return None
        props = publish.properties({"title": row[2], "company": row[3], "url": row[4], "source": row[5], "provider": row[6], "lane": row[7], "key": row[1],
                                    "first_seen": row[8].date() if hasattr(row[8], "date") else row[8], "posted": None, "fit": row[15], "fit_line": row[16],
                                    "admission": row[17], "admission_reason": row[18], "eligible": (row[23] or "").split(",")})
        return props["LIFE OS Fit"]["number"], props["Why It Fits"]["rich_text"][0]["text"]["content"]

    def test_a_valid_employer_ats_job_gets_its_cloud_score_as_the_visible_fit_and_reaches_the_ledger_properties(self):
        self.job(1)
        endpoint = Endpoint()
        counts = self.stage_run(self.CANARY, endpoint)
        self.assertEqual((counts["cloud_calls"], counts["cloud_ok"], counts["cloud_promoted"], len(endpoint.sent)), (1, 1, 1, 1))
        score, decision, line, admission, cloud_status = self.visible(1)
        self.assertEqual((score, decision, admission, cloud_status), (90, "Go", "ADMIT", "CANARY"))            # the fake Oracle judged 90
        self.assertTrue(line.startswith("Cloud Fit 90% (gpt-6-luna): "))
        self.assertEqual(self.row(1, ("cloud_score", "cloud_model")), (90, "gpt-6-luna"))
        self.assertEqual(self.published_fit(1), (90, line))                                     # the Ledger page would carry LIFE OS Fit 90 and the Cloud Fit line

    def test_a_low_cloud_score_is_judged_by_the_same_floor_and_gate_and_never_published(self):
        self.job(1)
        low = Endpoint(lambda data: reply(data, judgment_raw=json.dumps({"score": 41, "rationale": "Different discipline.", "confidence": "high"})))
        self.assertEqual(self.stage_run(self.CANARY, low)["cloud_promoted"], 1)
        score, decision, _, admission, _ = self.visible(1)
        self.assertEqual((score, decision, admission), (41, "No-Go", "EXCLUDE"))
        self.assertEqual(self.db.raw.execute("SELECT status FROM v7_jobs WHERE id=1").fetchone()[0], "EXCLUDED_FIT")
        self.assertIsNone(self.published_fit(1))

    def test_the_static_scorer_never_overwrites_a_canary_score(self):
        self.job(1)
        self.stage_run(self.CANARY, Endpoint())
        self.db.raw.execute("UPDATE v7_job_descriptions SET fingerprint='changed' WHERE job_id=1")      # a description change would normally re-score
        self.db.raw.execute("UPDATE v7_job_fit SET profile_hash='other' WHERE job_id=1")
        endpoint = Endpoint()
        counts = self.stage_run({**SHADOW, "V7_CLOUD_FIT": "off", "V7_FIT_GATE": "true"}, endpoint)
        self.assertEqual(counts["picked"], 0)
        self.assertEqual(self.visible(1)[0], 90)
        self.assertEqual(endpoint.sent, [])

    def test_no_failure_path_can_produce_a_visible_cloud_score(self):
        bad = {
            "refusal": (self.CANARY, Endpoint(lambda data: refusal(data, "DAILY_LIMIT"))),
            "purge failed": (self.CANARY, Endpoint(lambda data: reply(data, fixture="REAL", receipt={**json.loads(reply(data))["receipt"], "purge": "failed"}))),
            "receipt mismatch": (self.CANARY, Endpoint(lambda data: reply(data, receipt={**json.loads(reply(data))["receipt"], "fallback": True}))),
            "wrong hash": (self.CANARY, Endpoint(lambda data: reply(data, request_hash="0" * 64))),
            "unusable judgment": (self.CANARY, Endpoint(lambda data: reply(data, judgment_raw="not json"))),
            "no reply": (self.CANARY, Endpoint(lambda data: b"")),
            "missing profile reference": ({**self.CANARY, "V7_CLOUD_FIT_PROFILE_REF": ""}, Endpoint()),
            "missing Oracle host": ({**self.CANARY, "IMESSAGE_ALERTS_HOST": ""}, Endpoint()),
        }
        for label, (environ, endpoint) in bad.items():
            self.setUp()
            self.job(1)
            counts = self.stage_run(environ, endpoint)
            score, decision, line, admission, cloud_status = self.visible(1)
            self.assertEqual(counts["cloud_promoted"], 0, label)
            self.assertNotEqual(cloud_status, "CANARY", label)
            self.assertFalse((line or "").startswith("Cloud Fit"), label)
            self.assertNotEqual(score, 90, label)                                               # the visible Fit is the static scorer's

    def test_a_failed_promotion_after_a_valid_answer_leaves_the_named_failure_not_a_stuck_canary_and_stays_recoverable(self):
        self.job(1)
        self.stage_run({**self.CANARY, "V7_CLOUD_FIT": "off"}, Endpoint())
        static = self.visible(1)[:4]                                                            # what the static scorer alone makes visible
        self.setUp()
        self.job(1)
        endpoint = Endpoint()                                                                   # a valid Oracle answer (90) is received and stored
        with mock.patch.object(stage, "promote_cloud", return_value=False):
            counts = self.stage_run(self.CANARY, endpoint)
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], counts["cloud_ok"], counts["cloud_promoted"], len(endpoint.sent)),
                         ("DEGRADED(CANARY_PROMOTION_FAILED)", 1, 0, 0, 1))
        self.assertEqual(self.visible(1)[:4], static)                                           # no visible overwrite
        self.assertEqual(self.row(1, ("cloud_status", "cloud_reason")), ("DEGRADED", "CANARY_PROMOTION_FAILED"))   # no stuck CANARY
        published = self.published_fit(1)
        self.assertFalse(published and published[1].startswith("Cloud Fit"))                  # the Ledger would carry the static Fit, never the unpromoted answer
        again = Endpoint()
        self.db.raw.execute("UPDATE v7_job_descriptions SET fingerprint='changed' WHERE job_id=1")
        counts = self.stage_run(self.CANARY, again)
        self.assertEqual((counts["picked"], again.sent), (1, []))                              # the static scorer still owns it; the model is not asked again
        self.db.raw.execute("UPDATE v7_job_fit SET cloud_status=NULL, cloud_reason=NULL WHERE job_id=1")   # an operator clear makes it askable again
        self.assertEqual(self.stage_run(self.CANARY, Endpoint())["cloud_promoted"], 1)
        self.assertEqual(self.visible(1)[0::4], (90, "CANARY"))

    def test_an_answer_the_guarded_write_does_not_store_is_a_named_conflict_never_a_success_or_a_visible_score(self):
        self.job(1)
        self.stage_run({**self.CANARY, "V7_CLOUD_FIT": "off"}, Endpoint())
        static = self.visible(1)[:4]
        self.setUp()
        self.job(1)

        def racing(data):                                                                       # another writer records this row while the answer is in flight
            self.db.raw.execute("UPDATE v7_job_fit SET cloud_status='SKIPPED', cloud_reason='RACE' WHERE job_id=1")
            return reply(data)
        endpoint = Endpoint(racing)
        counts = self.stage_run(self.CANARY, endpoint)
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"], counts["cloud_ok"], counts["cloud_promoted"], len(endpoint.sent)),
                         ("DEGRADED(CANARY_WRITE_CONFLICT)", 1, 0, 0, 1))                       # WRITE rowcount 0: no success, no retry, no second call
        self.assertEqual(self.visible(1)[:4], static)                                           # the static result stays visible
        self.assertEqual(self.row(1, ("cloud_status", "cloud_reason", "cloud_score")), ("SKIPPED", "RACE", None))   # never CANARY, nothing of the answer stored
        published = self.published_fit(1)
        self.assertFalse(published and published[1].startswith("Cloud Fit"))

    def test_a_promotion_whose_gate_write_fails_fails_the_tick_loudly_and_the_next_tick_moves_the_job_without_asking_again(self):
        self.job(1)
        low = Endpoint(lambda data: reply(data, judgment_raw=json.dumps({"score": 41, "rationale": "Different discipline.", "confidence": "high"})))
        real = stage._gate

        def broken(cursor, job_id, decision, gate, counts):
            if decision.status == "EXCLUDE":                                                    # only the canary's gate write fails (the static pass admits this job)
                raise RuntimeError("store write failed")
            return real(cursor, job_id, decision, gate, counts)
        with mock.patch.object(stage, "_gate", side_effect=broken), self.assertRaises(RuntimeError):
            self.stage_run(self.CANARY, low)                                                    # never reported as success
        self.assertEqual((self.visible(1)[0], self.visible(1)[3], self.visible(1)[4]), (41, "EXCLUDE", "CANARY"))
        self.assertEqual(self.db.raw.execute("SELECT status FROM v7_jobs WHERE id=1").fetchone()[0], "READY")    # promoted but not yet gated
        endpoint = Endpoint()
        counts = self.stage_run(self.CANARY, endpoint)
        self.assertEqual((counts["canary_regated"], endpoint.sent), (1, []))
        self.assertEqual(self.db.raw.execute("SELECT status FROM v7_jobs WHERE id=1").fetchone()[0], "EXCLUDED_FIT")
        self.assertIsNone(self.published_fit(1))
        self.assertEqual(self.stage_run(self.CANARY, Endpoint())["canary_regated"], 0)            # idempotent

    def test_a_rejected_candidate_is_never_asked_or_promoted(self):
        self.job(1, url="https://boards.greenhouse.io/acme/jobs/search?q=program+manager")      # the audit would demote it
        endpoint = Endpoint()
        counts = self.stage_run(self.CANARY, endpoint)
        self.assertEqual((counts["cloud_calls"], counts["cloud_promoted"], endpoint.sent), (0, 0, []))
        self.assertEqual(self.visible(1)[4], "SKIPPED")


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
            self.setUp()
            self.job(1)
            counts = self.run_cloud(SHADOW, Endpoint(answer))
            self.assertEqual(counts["cloud_fit"], f"DEGRADED({code})")
            # D219: BUSY ran no model, so the claim is released and the job asked later; anything else may have run a model, so it is recorded, never repeated
            self.assertEqual(self.row(1, ("cloud_status", "cloud_reason")), (None, None) if code == "FIT_EDGE_BUSY" else ("DEGRADED", code), code)

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


class EgressProvenance(Base):
    """LIFE-41 10500 (TL review of #339): the description's reading method proves nothing about the site. The same source_kind is sent from an employer ATS or
    the employer's own board, and refused before any frame from Dice, LinkedIn, an aggregator, an unknown host reached through a newsletter, or no URL."""

    def test_the_same_reading_method_is_allowed_only_from_an_employer_or_its_ats(self):
        allowed = (("https://boards.greenhouse.io/acme/jobs/1", "jobright"), ("https://acme.wd1.myworkdayjobs.com/en-US/careers/job/R1", "linkedin"),
                   ("https://careers.acme.com/jobs/1", "web:acme"))
        blocked = (("https://www.dice.com/job-detail/abc", "dice"), ("https://www.linkedin.com/jobs/view/123", "linkedin"), ("https://jobright.ai/jobs/info/x", "jobright"),
                   ("https://www.lensa.com/job/1", "lensa"), ("https://talentfirm-recruiting.com/jobs/1", "jobright"), ("https://careers.acme.com/jobs/1", "web:openjobs"),
                   (None, "jobright"), ("", "jobright"))
        for kind in ("jsonld", "page_text", "ats_list"):                     # ats_list: the employer ATS board listing text (LIFE-41 G6)
            for url, source in allowed:
                self.setUp()
                self.job(1, kind=kind, url=url, source=source)
                endpoint = Endpoint()
                self.assertEqual(self.run_cloud(SHADOW, endpoint)["cloud_ok"], 1, (kind, url, source))
                self.assertEqual(len(endpoint.sent), 1)
            for url, source in blocked:
                self.setUp()
                self.job(1, kind=kind, url=url, source=source)
                endpoint = Endpoint()
                counts = self.run_cloud(SHADOW, endpoint)
                self.assertEqual((counts["cloud_calls"], counts["cloud_skipped"], endpoint.sent), (0, 1, []), (kind, url, source))
                self.assertEqual(self.row(1, ("cloud_status", "cloud_reason")), ("SKIPPED", "NOT_PUBLIC_POSTING"))


class ContactDetailsNeverLeave(Base):
    """LIFE-41 10500: a duties or requirements line with an email, phone, link or contact instruction is dropped before the frame is built."""
    TEXT = GOOD.replace("• Strong communication skills\n", "• Strong communication skills\n• Questions? Email jane.doe@example.com or call +44 20 7946 0958\n"
                        "• Apply via https://apply.example/xyz or reach out to the recruiter on linkedin.com/in/janedoe\n• Contact us at hiring@example.com with 8+ years of experience\n")

    def test_the_sent_description_carries_no_contact_detail_and_keeps_the_role(self):
        self.assertNotEqual(self.TEXT, GOOD)
        self.job(1, text=self.TEXT)
        endpoint = Endpoint()
        self.assertEqual(self.run_cloud(SHADOW, endpoint)["cloud_ok"], 1)
        sent = json.loads(endpoint.sent[0].split(b"\n", 1)[1])["request"]
        body = json.dumps(sent).lower()
        for private in ("jane.doe", "@", "7946", "+44", "https://", "linkedin", "recruiter", "hiring@", "contact us"):
            self.assertNotIn(private, body, private)
        self.assertIn("program roadmap", sent["description"].lower())                         # the role's own duties still go

    def test_a_title_with_a_contact_detail_sends_nothing(self):
        self.job(1)
        self.db.raw.execute("UPDATE v7_jobs SET title=? WHERE id=1", ("Technical Program Manager - email jane@example.com",))
        endpoint = Endpoint()
        counts = self.run_cloud(SHADOW, endpoint)
        self.assertEqual((counts["cloud_calls"], counts["cloud_skipped"], endpoint.sent), (0, 1, []))


class TitlePlaceNeverLeaves(Base):
    def test_an_unseparated_place_in_the_title_is_skipped_with_no_network_call(self):
        self.job(1)
        self.db.raw.execute("UPDATE v7_jobs SET title=? WHERE id=1", ("Senior Technical Program Manager London",))
        endpoint = Endpoint()
        counts = self.run_cloud(SHADOW, endpoint)
        self.assertEqual((counts["cloud_calls"], counts["cloud_skipped"], endpoint.sent), (0, 1, []))
        self.assertEqual(self.row(1, ("cloud_status", "cloud_reason")), ("SKIPPED", "TITLE_LOCATION"))

    def test_a_clean_role_title_is_still_sent(self):
        self.job(1)
        self.db.raw.execute("UPDATE v7_jobs SET title=? WHERE id=1", ("Senior Technical Program Manager",))
        endpoint = Endpoint()
        self.assertEqual(self.run_cloud(SHADOW, endpoint)["cloud_ok"], 1)
        self.assertEqual(json.loads(endpoint.sent[0].split(b"\n", 1)[1])["request"]["title"], "Senior Technical Program Manager")


class DegradedIsAFailedFitStage(Base):
    """TL review of #339: an enabled shadow that fails makes the Fit stage fail through the existing run.PARTIAL_FAILURE exit; off never does. Static Fit is written either way."""

    def stage_run(self, environ, endpoint):
        self.job(1)
        self.db.raw.execute("DELETE FROM v7_job_fit")
        with mock.patch.object(stage.store, "connect", return_value=self.db), mock.patch.object(stage.store, "ensure_schema"), \
                mock.patch.object(stage.fit_profile, "load", return_value=PROFILE), mock.patch.object(stage.sponsors, "load", return_value=None), \
                mock.patch.object(cloud.store, "connect", return_value=self.db), mock.patch.object(edge, "exchange", endpoint):
            return stage.run(10, True, environ)

    def test_off_leaves_the_fit_stage_green_and_a_shadow_refusal_fails_it(self):
        off = self.stage_run({}, Endpoint())
        self.assertEqual((off["cloud_fit"], off["failed"]), ("off", 0))
        self.setUp()
        refused = self.stage_run(SHADOW, Endpoint(lambda data: refusal(data, "DAILY_LIMIT")))
        self.assertEqual((refused["cloud_fit"], refused["failed"]), ("DEGRADED(FIT_EDGE_REFUSED_DAILY_LIMIT)", 1))
        self.assertEqual(self.row(1, ("decision", "cloud_status"))[1], None)                    # the static Fit is stored; no cloud answer is claimed
        self.assertIsNotNone(self.row(1, ("score",))[0])
        self.setUp()
        ok = self.stage_run(SHADOW, Endpoint())
        self.assertEqual((ok["cloud_fit"], ok["failed"]), ("OK", 0))
        for counts, code in ((off, 0), (refused, 1), (ok, 0)):
            with mock.patch.dict(lifeos_run.STAGES, {"fit": lambda limit, live, counts=counts: counts}):
                import contextlib, io
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(lifeos_run.main(["fit"]), code)
            self.assertEqual(err.getvalue(), "FIT FAILED: FIT_CLOUD_DEGRADED\n" if code else "")


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


class OracleContract10504(Base):
    """LIFE-41 10504: the installed Oracle fit1 front (front.py 8e8d724b…) as V7 sees it. Synthetic stub vectors only; no Oracle or model is reached."""
    VECTOR = {"title": "Technical Program Manager", "description": "Synthetic duty: lead delivery of an invented platform migration across five teams.",
              "profile": {"ref": "976d1682938e988e"}}

    def receipt(self, **extra):
        from tests.jobs.fit.test_edge import RECEIPT
        return {**RECEIPT, **extra}

    def test_the_request_hash_matches_oracle_for_the_real_shaped_vector(self):
        self.assertEqual(edge.digest(self.VECTOR), "34cf89e0a24582ba05e6df0b710e815f625767384a2ae131e2481445f83c52e4")

    def test_the_frame_nonce_and_read_wait_are_as_contracted(self):
        self.job(1)
        self.job(2, seen=T + datetime.timedelta(hours=1))
        seen = []

        def endpoint(host, data, wait=None):
            seen.append((data, wait))
            return reply(data)
        self.run_cloud(SHADOW, endpoint)
        self.run_cloud(SHADOW, endpoint)
        nonces = []
        for data, wait in seen:
            head, body = data.split(b"\n", 1)
            self.assertEqual(head, b"fit1 %d" % len(body))
            framed = json.loads(body)
            self.assertEqual(set(framed), {"request", "nonce"})
            self.assertRegex(framed["nonce"], r"^[0-9a-f]{32}$")
            self.assertEqual(set(framed["request"]), {"title", "description", "profile"})
            self.assertEqual(framed["request"]["profile"], {"ref": REF})
            self.assertGreaterEqual(wait, 150)
            nonces.append(framed["nonce"])
        self.assertEqual(len(set(nonces)), 2)                                                    # fresh for every request

    def test_busy_without_hash_or_nonce_is_busy_not_a_hash_mismatch(self):
        self.job(1)
        counts = self.run_cloud(SHADOW, Endpoint(lambda data: json.dumps({"schema": "lifeos.fit.v1", "status": "BUSY"}).encode()))
        self.assertEqual((counts["cloud_fit"], counts["cloud_calls"]), ("DEGRADED(FIT_EDGE_BUSY)", 1))
        self.assertIsNone(self.row(1, ("cloud_status",))[0])                                     # asked again on a later tick, never a score

    def test_every_installed_refusal_reason_keeps_its_fixed_code_and_never_scores(self):
        for reason in ("NOT_ALLOWLISTED", "REAL_DISABLED", "BRIEF_MISSING", "BRIEF_UNSAFE", "BRIEF_HASH_MISMATCH", "BRIEF_INVALID", "PROFILE_MISMATCH",
                       "LIMIT_UNCONFIGURED", "PURGE_FAILED", "NONCE_REPLAYED", "COUNTER_UNAVAILABLE", "DAILY_LIMIT"):
            self.setUp()
            self.job(1)
            counts = self.run_cloud(SHADOW, Endpoint(lambda data, reason=reason: refusal(data, reason)))
            self.assertEqual(counts["cloud_fit"], f"DEGRADED(FIT_EDGE_REFUSED_{reason})", reason)
            self.assertEqual(self.row(1, ("cloud_status", "cloud_score")), (None, None), reason)

    def test_accepted_real_reply_with_purge_ok_is_stored(self):
        self.job(1)
        counts = self.run_cloud(SHADOW, Endpoint(lambda data: reply(data, fixture="REAL", receipt=self.receipt(purge="ok"))))
        self.assertEqual(counts["cloud_fit"], "OK")
        self.assertEqual(self.row(1, ("cloud_status", "cloud_score")), ("OK", 90))

    def test_a_failed_purge_is_degraded_keeps_provenance_without_a_score_and_stops(self):
        self.job(1)
        self.job(2, seen=T + datetime.timedelta(hours=1))
        endpoint = Endpoint(lambda data: reply(data, fixture="REAL", receipt=self.receipt(purge="failed")))
        counts = self.run_cloud(SHADOW, endpoint)
        self.assertEqual((counts["cloud_fit"], counts["cloud_ok"], len(endpoint.sent)), ("DEGRADED(FIT_EDGE_PURGE_FAILED)", 0, 1))
        status, reason, score, confidence, rationale, request_hash, model, run_id, fingerprint, at = self.row(1, CLOUD)
        self.assertEqual((status, reason, score, confidence, rationale, model, run_id), ("DEGRADED", "FIT_EDGE_PURGE_FAILED", None, None, None, "gpt-6-luna", "run-1234567890"))
        self.assertTrue(request_hash and at)
        self.assertIsNone(self.row(2, ("cloud_status",))[0])

    def test_a_real_reply_without_purge_and_an_unknown_purge_value_are_rejected(self):
        for receipt in (self.receipt(), self.receipt(purge="maybe")):
            self.setUp()
            self.job(1)
            counts = self.run_cloud(SHADOW, Endpoint(lambda data, receipt=receipt: reply(data, fixture="REAL", receipt=receipt)))
            self.assertEqual(counts["cloud_fit"], "DEGRADED(FIT_EDGE_RECEIPT_REJECTED)")
            self.assertEqual(self.row(1, ("cloud_status", "cloud_reason", "cloud_score")), ("DEGRADED", "FIT_EDGE_RECEIPT_REJECTED", None))     # D219: never re-asked

    def test_a_synthetic_receipt_without_purge_still_passes(self):
        self.assertIsNone(edge.bad_receipt_field(self.receipt()))
        self.assertNotIn("purge", self.receipt())

    def test_unavailable_malformed_and_timeout_states(self):
        def timed_out(data):
            raise edge.EdgeError("FIT_EDGE_TIMEOUT")
        for answer, code in ((lambda data: b"", "FIT_EDGE_NO_REPLY"), (lambda data: b"not json", "FIT_EDGE_OFF_CONTRACT"),
                             (lambda data: json.dumps({"schema": "other", "status": "OK"}).encode(), "FIT_EDGE_OFF_CONTRACT"),
                             (timed_out, "FIT_EDGE_TIMEOUT"), (lambda data: reply(data, status="TIMEOUT", judgment_raw=None), "FIT_EDGE_TIMEOUT"),
                             (lambda data: reply(data, status="TIMEOUT_ORPHAN", judgment_raw=None), "FIT_EDGE_TIMEOUT_ORPHAN"),
                             (lambda data: reply(data, status="ERROR", judgment_raw=None), "FIT_EDGE_ERROR")):
            self.setUp()
            self.job(1)
            endpoint = Endpoint(answer)
            counts = self.run_cloud(SHADOW, endpoint)
            self.assertEqual((counts["cloud_fit"], len(endpoint.sent)), (f"DEGRADED({code})", 1), code)     # one attempt, no retry
            self.assertEqual(self.row(1, ("cloud_status", "cloud_reason", "cloud_score")), ("DEGRADED", code, None), code)   # D219: a model may have run: never re-asked

    def test_the_call_respects_the_ticks_actual_remaining_alarm(self):
        import signal
        self.job(1)
        previous = signal.signal(signal.SIGALRM, lambda *a: None)
        try:
            for alarm, calls in ((100, 0), (200, 1)):                                            # a short tick cut the stage to 100 s: one 165 s call cannot start
                signal.alarm(alarm)
                counts = self.run_cloud(SHADOW, Endpoint())
                signal.alarm(0)
                self.assertEqual(counts["cloud_calls"], calls, alarm)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        self.assertEqual(cloud.CALL_SECONDS, 165)


class StaticPublishingIsNeverSuppressed(unittest.TestCase):
    def test_a_failed_fit_stage_does_not_stop_publish_or_the_rest_of_the_tick(self):
        """D214: an enabled shadow that fails makes `fit` exit 1; the tick still runs audit, publish and finalize (each stage independent), and reports fit=failed."""
        from lifeos.jobs import tick
        ran = []
        stages = {s.name: (lambda limit, live, name=s.name: ran.append(name) or (1 if name == "fit" else 0)) for s in tick.PLAN}
        finalize = mock.Mock(main=lambda argv: ran.append("finalize") or 0)                    # the module stage (finalize) is stubbed the same way
        with mock.patch.object(tick.importlib, "import_module", return_value=finalize):
            outcome = tick.tick(True, stages=stages)
        self.assertEqual(outcome["fit"], "failed")
        for name in ("audit", "publish", "expire-holds", "finalize"):
            self.assertIn(name, ran)
            self.assertEqual(outcome[name], "ok")
