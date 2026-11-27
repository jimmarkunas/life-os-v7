"""(AC04, AC07, AC08): one Stable Job Key makes at most one Ledger row from every creator"""
import glob
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta

from lifeos.jobs import fit_sync, identity, publish
from lifeos.platform import ledger_claim as lc
from lifeos.sources.newsletters import finalize
from tests.jobs.fakes_jobsdb import JobsDB

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 22, 12, 0)
KEY = "a" * 64


class Claim(unittest.TestCase):
    def setUp(self):
        self.db = JobsDB()
        self.connect = lambda: self.db

    def claim(self, key=KEY, who="x", now=NOW):
        return lc.claim(key, who, self.connect, now)

    def test_the_first_claimant_wins_and_everyone_else_is_held(self):
        self.assertEqual(self.claim(who="publish"), (lc.WON, None))
        self.assertEqual(self.claim(who="lead"), (lc.HELD, None))
        self.assertEqual(self.claim(who="opportunity"), (lc.HELD, None))

    def test_a_key_with_no_job_gets_a_placeholder_that_no_stage_picks_and_a_real_job_is_left_as_it_is(self):
        self.claim()
        self.assertEqual(self.db.raw.execute("SELECT status, title, notion_page_id FROM v7_jobs").fetchall(), [("LEDGER", None, None)])
        self.db.insert("v7_jobs", dedupe_key="b" * 64, status="READY", title="Program Manager", company="Acme", first_seen=NOW, last_seen=NOW, updated_at=NOW)
        self.assertEqual(self.claim("b" * 64)[0], lc.WON)
        self.assertEqual(self.db.raw.execute("SELECT status, title, company, ledger_claim_at IS NOT NULL FROM v7_jobs WHERE dedupe_key=?", ("b" * 64,)).fetchone(), ("READY", "Program Manager", "Acme", 1))

    def test_a_recorded_page_is_handed_back_never_created_again(self):
        self.claim()
        self.db.raw.execute("UPDATE v7_jobs SET notion_page_id='page-1', ledger_claim_by=NULL, ledger_claim_at=NULL")
        self.assertEqual(self.claim(who="lead"), (lc.CREATED, "page-1"))

    def test_only_the_holder_releases_and_a_release_lets_the_next_claimant_win(self):
        self.claim(who="publish")
        lc.release(KEY, "someone-else", self.connect)
        self.assertEqual(self.claim(who="lead")[0], lc.HELD)
        lc.release(KEY, "publish", self.connect)
        self.assertEqual(self.claim(who="lead")[0], lc.WON)

    def test_an_unreleased_claim_is_taken_over_only_after_the_lease(self):
        self.claim(who="publish")
        self.assertEqual(self.claim(who="lead", now=NOW + lc.LEASE - timedelta(seconds=1))[0], lc.HELD)
        self.assertEqual(self.claim(who="lead", now=NOW + lc.LEASE + timedelta(seconds=1))[0], lc.WON)

    def test_the_role_key_is_a_hash_of_company_and_role_and_never_the_names(self):
        key = lc.role_key("Acme Corp", "Program Manager")
        self.assertEqual((len(key), key == lc.role_key("ACME  corp", "program manager"), key == lc.role_key("Acme Corp", "Product Manager")), (64, True, False))
        self.assertNotIn("acme", key)

    def test_the_new_columns_are_nullable_and_additive(self):
        from lifeos.jobs import store
        added = {c: ddl for t, c, ddl, _ in store.COLUMNS if c.startswith("ledger_claim")}
        self.assertEqual(added, {"ledger_claim_by": "VARCHAR(64) NULL", "ledger_claim_at": "DATETIME NULL"})


class TwoProcessRace(unittest.TestCase):
    """Real OS processes, one store file, one ledger file standing in for Notion."""

    def race(self, mode, roles):
        with tempfile.TemporaryDirectory() as tmp:
            store_file, ledger_file = os.path.join(tmp, "store.db"), os.path.join(tmp, "ledger.txt")
            JobsDB(store_file)  # the schema, once
            open(ledger_file, "w").close()
            go_file = os.path.join(tmp, "go")
            procs = [subprocess.Popen([sys.executable, "-B", "-m", "tests.jobs.claim_race_worker", store_file, ledger_file, KEY, role, go_file, mode, str(len(roles))], cwd=ROOT, stdout=subprocess.PIPE, text=True)
                     for role in roles]
            deadline = time.monotonic() + 60
            while len(glob.glob(go_file + ".ready.*")) < len(roles) and time.monotonic() < deadline:
                time.sleep(0.005)
            open(go_file, "w").close()
            outs = [p.communicate(timeout=120)[0].strip() for p in procs]
            return outs, pathlib.Path(ledger_file).read_text().split()

    def test_without_the_claim_two_processes_make_two_rows(self):
        outs, rows = self.race("naive", ("publish", "lead"))
        self.assertEqual(len(rows), 2)  # the failure mode exists for

    def test_with_the_claim_publish_interview_lead_and_the_opportunity_creator_make_exactly_one_row(self):
        outs, rows = self.race("claim", ("publish", "lead", "opportunity", "publish"))
        self.assertEqual(rows, [KEY])
        self.assertEqual(sorted(outs), [lc.HELD, lc.HELD, lc.HELD, lc.WON])  # one winner


class Finalize(unittest.TestCase):
    """A mail is finalized as DUPLICATE only once the job it duplicates has a Ledger row the read-back verified."""

    def setUp(self):
        self.db, self.n = JobsDB(), 0

    def job(self, status, verified=None, twin=None):
        self.n += 1
        self.db.insert("v7_jobs", dedupe_key=f"{self.n:064d}", status=status, title="t", company="c", first_seen=NOW, last_seen=NOW, updated_at=NOW, verified_at=verified, repost_of=twin)
        return self.db.raw.execute("SELECT MAX(id) FROM v7_jobs").fetchone()[0]

    def mail(self, message_id, job_id):
        self.db.insert("v7_job_sources", job_id=job_id, source="x", source_url_hash=f"{self.n}{message_id}", gmail_message_id=message_id, seen_at=NOW)

    def blockers(self, *ids):
        return finalize.blockers(self.db, list(ids))

    def test_a_duplicate_of_a_verified_published_twin_closes_the_mail(self):
        survivor = self.job("PUBLISHED", NOW)
        self.mail("m1", self.job("DUPLICATE", twin=survivor))
        self.assertEqual(self.blockers("m1"), {})

    def test_a_duplicate_of_an_unverified_unpublished_or_missing_twin_keeps_the_mail_open(self):
        for label, survivor in (("unverified", self.job("PUBLISHED", None)), ("unpublished", self.job("READY")), ("missing", 9999), ("held", self.job("HOLD"))):
            self.mail(label, self.job("DUPLICATE", twin=survivor))
            self.assertEqual(self.blockers(label), {label: "twin_unverified"}, label)

    def test_a_twin_that_is_itself_a_duplicate_is_followed_to_its_verified_survivor(self):
        root = self.job("PUBLISHED", NOW)
        middle = self.job("DUPLICATE", twin=root)
        self.mail("m1", self.job("DUPLICATE", twin=middle))
        self.assertEqual(self.blockers("m1"), {})
        loop = self.job("DUPLICATE", twin=None)
        self.mail("m2", self.job("DUPLICATE", twin=loop))
        self.assertEqual(self.blockers("m2"), {"m2": "twin_unverified"})

    def test_a_duplicate_with_no_twin_recorded_stays_terminal_as_before(self):
        self.mail("m1", self.job("DUPLICATE"))  # in_ledger: the Ledger row exists outside the store
        self.assertEqual(self.blockers("m1"), {})


class ShortTitleTwins(unittest.TestCase):
    """a title of fewer than three words converges only on a proven equal ATS vacancy id."""

    def setUp(self):
        self.db, self.n = JobsDB(), 0

    def published(self, title, url, company="Acme"):
        self.n += 1
        self.db.insert("v7_jobs", dedupe_key=identity.url_hash(url + str(self.n)), status="PUBLISHED", title=title, company=company, source="web:x", source_url=url, final_apply_url=url,
                       first_seen=NOW, last_seen=NOW, updated_at=NOW, lane="US Remote", notion_page_id=f"p{self.n}")
        return self.db.raw.execute("SELECT MAX(id) FROM v7_jobs").fetchone()[0]

    def same(self, title, url, company="Acme", location=None):
        with self.db.cursor() as cursor:
            return publish.same_opening(cursor, 999, company, title, url, location)

    def test_two_observations_of_one_ats_vacancy_converge_in_one_run(self):
        first = self.published("Product Manager", "https://boards.greenhouse.io/acme/jobs/7007")
        self.assertEqual(self.same("Product Manager", "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=7007"), first)

    def test_two_different_vacancies_with_a_short_title_stay_two_and_no_id_or_one_sided_id_is_not_proof(self):
        self.published("Product Manager", "https://boards.greenhouse.io/acme/jobs/7007")
        self.assertIsNone(self.same("Product Manager", "https://boards.greenhouse.io/acme/jobs/8008"))
        self.assertIsNone(self.same("Product Manager", "https://www.linkedin.com/jobs/view/4478543856"))
        self.assertIsNone(self.same("Product Manager", "https://careers.acme.example/pm"))
        self.published("Designer", "https://careers.acme.example/designer")
        self.assertIsNone(self.same("Designer", "https://careers.acme.example/designer-2"))

    def test_a_short_title_is_never_merged_on_an_aggregators_place(self):
        self.published("Project Manager", "https://acme.wd5.myworkdayjobs.com/ext/job/Chicago-IL/Project-Manager_JR1001")
        self.assertIsNone(self.same("Project Manager", "https://www.linkedin.com/jobs/view/4482175447", location="Chicago, IL"))


class UpdatePathsKeepHumanState(unittest.TestCase):
    HUMAN = ("Applied", "Applied On", "Saturn Decision", "Stage", "Job", "Apply URL", "Company", "Stable Job Key")

    def test_fit_sync_never_writes_applied_applied_on_saturn_decision_stage_or_identity(self):
        for admission in ("ADMIT", "REVIEW"):
            for gated in (True, False):
                props = fit_sync.properties(81, "line", admission, "why", "remote", "US Remote", "US Remote", gated)
                self.assertFalse(set(props) & set(self.HUMAN), (admission, gated))

    def test_an_adopted_applied_page_is_only_filled_never_overwritten(self):
        import inspect
        source = inspect.getsource(publish.run)
        self.assertIn("body.fill(client, page_id, desc, strict=False)", source)  # an adopted page gets its body completed
        self.assertEqual(source.count('"PATCH"'), 0)

    def test_the_lead_row_only_ever_creates_and_an_existing_row_is_left_untouched(self):
        from lifeos.sources.interview import interview_evidence as ie
        from unittest import mock
        db = JobsDB()
        writer = mock.Mock()
        with mock.patch.object(ie.jobs_ledger, "verify", return_value=ie.jobs_ledger.OK), mock.patch.object(ie.publish, "existing_page", return_value="page-applied"), \
                mock.patch.object(lc.db, "connect", lambda: db):
            self.assertEqual(ie.lead_row(writer, "Acme", "Program Manager", KEY, "https://x", NOW.date()), "present")
        writer.create.assert_not_called()
        writer.call.assert_not_called()  # no PATCH of the existing row either


if __name__ == "__main__":
    unittest.main()
