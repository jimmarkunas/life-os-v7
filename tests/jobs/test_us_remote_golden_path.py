"""US_REMOTE_GOLDEN_PATH (LIFE-59 M5D): employer-board vacancies and a job-alert newsletter through the REAL shared V7 Jobs stages to the Job Ledger.

RUNTIME DECLARATION. ENTRY POINT: the hourly `boards` stage (lifeos.sources.web.boards.run) beside the newsletter chain of NEWSLETTER_GOLDEN_PATH_GATE, then
enrich, fit and publish (with its read-back) as `python -m lifeos.run` calls them. CALL CHAIN: boards.run -> web.run -> SqlRepo.commit -> intake.add_job ->
enrich.run -> fit.stage.run (lanes) -> publish.run (same_opening, v7_ledger_urls, existing_page, readback). MUTATION OWNER: the Jobs store (v7_*) and publish for
the one Ledger page per qualifying vacancy. CANONICAL STATE: v7_jobs / v7_job_fit / v7_sources and the Ledger page. AUTHORITATIVE READ-BACK: readback.check.
LIVE ACCEPTANCE BOUNDARY: the real boards, the real Ledger and Jim's acceptance (not claimed here).

Stood in for, as external boundaries: the ATS listing and employer pages (canned), Gmail and Notion (the gate's stateful fakes). Invented companies and the
gate's synthetic profile only (D16). TinyFish and paid browser usage raise if reached."""
from datetime import date, datetime, timedelta
import json
from unittest import mock
import unittest

from lifeos.jobs import enrich, identity
from lifeos.platform.http import Fetched
from lifeos.sources.web import boards, lister
from tests.jobs import test_newsletter_golden_path as gate

NOW = datetime.now().replace(microsecond=0)
BOARD = {"id": "gh-acme", "company": "Acme", "tier": "employer", "kind": "greenhouse", "status": "ready", "enabled": True, "slug": "acme",
         "refresh_hours": 6, "hot_hours": 1, "quiet": True}
SHARED = gate.VACANCIES["v1"][2]                       # the employer page the newsletter's Acme card resolves to: one opening, two providers

# board job id -> (title, place, url, page body or None for an employer page that is gone)
LISTED = {
    "7001": ("Senior Technical Program Manager", "Remote - US", "https://boards.greenhouse.io/acme/jobs/7001", gate.PM_BODY),     # qualifies
    "7002": ("Staff Software Engineer", "Remote - US", "https://boards.greenhouse.io/acme/jobs/7002", gate.ENGINEER_BODY),        # not the profession
    "7003": ("Technical Program Manager", "Remote", "https://boards.greenhouse.io/acme/jobs/7003", gate.PM_BODY),                 # US eligibility unknown
    "7004": ("Senior Program Manager", "Remote, US", SHARED, gate.PM_BODY),                                                      # also in the newsletter
    "7005": ("Delivery Program Manager", "Remote - US", "https://boards.greenhouse.io/acme/jobs/7005", None),                     # no employer evidence
}


def listing(source):
    jobs = [{"id": i, "title": t, "location": where, "url": url, "posted": date.today() - timedelta(days=2), "content": None, "company": None}
            for i, (t, where, url, _) in LISTED.items()]
    return lister.Listing(lister.COMPLETE, jobs)


def fetch(url, **kwargs):
    for ident, (title, _, page_url, body) in LISTED.items():               # the board's own job API is the authoritative description (enrich._api_job)
        if url == f"https://boards-api.greenhouse.io/v1/boards/acme/jobs/{ident}" and body:
            return Fetched(url, 200, json.dumps({"title": title, "content": body, "absolute_url": page_url, "first_published": date.today().isoformat()}))
    for company, title, final, body in gate.VACANCIES.values():
        if url == final:
            return Fetched(url, 200, gate.page(title, company, body))
    return Fetched(url, 404, "")


class UsRemoteGoldenPath(unittest.TestCase):
    def setUp(self):
        self.j = gate.Journey(self, {"m1": gate.mail("v1", "v2", "v3")})      # one alert, three vacancies, among them the Acme opening the board also lists
        patch = mock.patch.object(enrich, "fetch", fetch)
        patch.start()
        self.addCleanup(patch.stop)

    def board_pass(self):
        return boards.run(40, True, NOW, lister_fn=listing, sources=[BOARD])

    def run_all(self):
        self.j.resolve_all(["v1", "v2", "v3"])
        self.j.tick()                                    # the newsletter first: its Acme vacancy is published from the alert
        board = self.board_pass()
        enrich = gate.enrich.run(50, True)
        gate.fit_stage.run(100, True, gate.ENV)
        published = gate.publish.run(60, True, gate.ENV)
        return board, enrich, published

    def row(self, url):
        return self.j.db.raw.execute("SELECT j.status, j.unresolved_reason, j.notion_page_id IS NOT NULL, j.verified_at IS NOT NULL, f.admission, f.admission_reason,"
                                     " j.location_text, j.final_apply_url FROM v7_jobs j LEFT JOIN v7_job_fit f ON f.job_id=j.id WHERE j.dedupe_key=?",
                                     (identity.url_hash(url),)).fetchone()

    def pages(self):
        return {p["props"]["Apply URL"]["url"]: p["props"] for p in self.j.ledger.pages.values()}

    def test_a_qualifying_board_vacancy_reaches_the_ledger_with_its_employer_link_key_fit_and_read_back(self):
        self.run_all()
        status, _, has_page, verified, admission, _, place, final = self.row(LISTED["7001"][2])
        self.assertEqual((status, has_page, verified, admission, final), ("PUBLISHED", True, True, "ADMIT", LISTED["7001"][2]))
        props = self.pages()[LISTED["7001"][2]]
        self.assertEqual(props["Stable Job Key"]["rich_text"][0]["plain_text"], identity.url_hash(LISTED["7001"][2]))
        self.assertEqual((props["Admission Status"]["select"]["name"], props["Visible Lane"]["select"]["name"]), ("Admitted", "US Remote"))
        self.assertGreaterEqual(props["LIFE OS Fit"]["number"], 72)

    def test_an_irrelevant_profession_is_never_stored_and_never_published(self):
        board, _, _ = self.run_all()
        self.assertIsNone(self.row(LISTED["7002"][2]))
        self.assertNotIn(LISTED["7002"][2], self.pages())

    def test_unknown_us_eligibility_is_review_with_its_place_kept_never_an_admit(self):
        self.run_all()
        status, _, _, _, admission, reason, place, _ = self.row(LISTED["7003"][2])
        self.assertEqual((admission, reason, place), ("REVIEW", "market unresolved", "Remote"))
        page = self.pages().get(LISTED["7003"][2])
        self.assertTrue(page is None or page["Admission Status"]["select"]["name"] != "Admitted")

    def test_one_opening_from_the_newsletter_and_the_board_is_one_ledger_page(self):
        self.run_all()
        acme = [p for p in self.j.ledger.pages.values() if p["props"]["Apply URL"]["url"] == SHARED]
        self.assertEqual(len(acme), 1)
        status, reason = self.row(SHARED)[:2]
        self.assertEqual((status, reason), ("DUPLICATE", "in_ledger"))                    # the board's row is set aside: the alert's page stands

    def test_a_vacancy_without_employer_evidence_fails_closed(self):
        self.run_all()
        status, _, has_page = self.row(LISTED["7005"][2])[:3]
        self.assertNotIn(status, ("READY", "PUBLISHED"))
        self.assertFalse(has_page)

    def test_a_replay_adds_no_job_and_no_page(self):
        self.run_all()
        jobs, pages = self.j.db.raw.execute("SELECT COUNT(*) FROM v7_jobs").fetchone()[0], len(self.j.ledger.pages)
        self.board_pass()
        gate.enrich.run(50, True)
        gate.fit_stage.run(100, True, gate.ENV)
        gate.publish.run(60, True, gate.ENV)
        self.j.tick()
        self.assertEqual((self.j.db.raw.execute("SELECT COUNT(*) FROM v7_jobs").fetchone()[0], len(self.j.ledger.pages)), (jobs, pages))


if __name__ == "__main__":
    unittest.main()


# LIFE-59 M6: the vacancy's ATS id is its identity. 8001 reaches the newsletter as Greenhouse's embed link and the board as its board page.
# (8002 and 8003, two requisitions with one company, title and place, are held back at intake by repost.link: see the M6 report.)
REQS = {i: ("Senior Technical Program Manager", "Remote - US", f"https://boards.greenhouse.io/acme/jobs/{i}", gate.PM_BODY) for i in ("8001", "8002", "8003")}
EMBED_8001 = "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=8001"


class VacancyIdentity(unittest.TestCase):
    def setUp(self):
        self.j = gate.Journey(self, {"m1": gate.mail("v2")})
        for p in (mock.patch.object(enrich, "fetch", fetch), mock.patch.dict(LISTED, REQS, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.j.resolved["https://jobright.ai/jobs/info/v2"] = {"outcome": "landed", "url": EMBED_8001, "kind": "ats", "via": "original_post"}

    def run_all(self):
        self.j.tick()
        boards.run(40, True, NOW, lister_fn=listing, sources=[BOARD])
        gate.enrich.run(50, True)
        gate.fit_stage.run(100, True, gate.ENV)
        return gate.publish.run(60, True, gate.ENV)

    def pages_for(self, ident):
        return [p for p in self.j.ledger.pages.values() if identity.vacancy_id(p["props"]["Apply URL"]["url"]) == "greenhouse:" + ident]

    def test_one_vacancy_from_the_newsletter_and_the_board_is_one_page(self):
        self.run_all()
        self.assertEqual(len(self.pages_for("8001")), 1)
        board_row = self.j.db.raw.execute("SELECT status, unresolved_reason FROM v7_jobs WHERE dedupe_key=?", (identity.url_hash(REQS["8001"][2]),)).fetchone()
        self.assertEqual(tuple(board_row), ("DUPLICATE", "in_ledger"))
