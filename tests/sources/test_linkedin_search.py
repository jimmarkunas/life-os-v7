"""D203: LinkedIn job search works in concert with the LinkedIn job alerts: one identity, one resolve lane (alerts first), no seen_count bumps,
and the first refusal stops it with a named reason."""
from datetime import date, datetime
import unittest

from lifeos.jobs import identity
from lifeos.jobs.resolve import stage
from lifeos.platform.http import Fetched
from lifeos.sources import linkedin_search as ls
from tests.kit.jobsdb import JobsDB

NOW = datetime(2026, 10, 17, 22, 0)


def card(jid, title, company="Acme", where="United States", day="2026-10-17"):
    return (f'<li><div data-entity-urn="urn:li:jobPosting:{jid}"><h3 class="base-search-card__title">  {title} </h3>'
            f'<h4 class="base-search-card__subtitle"><a href="x">{company}</a></h4><span class="job-search-card__location">{where}</span>'
            f'<time class="job-search-card__listdate" datetime="{day}">1 day ago</time></div></li>')


def pages(*bodies):
    """A fake fetch answering successive requests with (status, html)."""
    queue, seen = list(bodies), []

    def fake(url, **kwargs):
        seen.append(url)
        status, body = queue.pop(0) if queue else (200, "")
        return Fetched(url, status, body)
    fake.seen = seen
    return fake


class Parse(unittest.TestCase):
    def test_a_card_becomes_the_newsletters_canonical_url(self):
        got = ls.parse(card(4463152620, "Senior Technical Program Manager &amp; Lead", "Ripple", "United States"))
        self.assertEqual(got, [{"id": "4463152620", "url": "https://www.linkedin.com/jobs/view/4463152620", "title": "Senior Technical Program Manager & Lead",
                                "company": "Ripple", "location": "United States", "posted": date(2026, 10, 17)}])

    def test_the_search_is_role_first_with_no_work_mode_filter_or_keyword(self):
        url = ls.search_url("program manager", 10)
        self.assertNotIn("f_WT", url)
        self.assertIn("keywords=program+manager&", url)
        self.assertIn("f_TPR=r86400", url)
        self.assertEqual([q for q in ls.QUERIES if "remote" in q.split()], [])      # D204: a job that never says remote is still a candidate


class Read(unittest.TestCase):
    def test_the_first_refusal_stops_and_is_named(self):
        for status, why in ((429, "rate_limited_429"), (403, "blocked_403"), (999, "blocked_999"), (500, "http_500"), (0, "network")):
            with self.subTest(status=status):
                fetch = pages((200, "".join(card(100000 + i, "Program Manager") for i in range(10))), (status, ""))
                cards, counts = ls.read(fetch, pause=0, pages=4)
                self.assertEqual((len(cards), counts["cards"], counts["pages"], counts["stopped"]), (10, 10, 2, why))
                self.assertEqual(len(fetch.seen), 2)

    def test_a_changed_page_shape_is_named_not_read_as_empty(self):
        _, counts = ls.read(pages((200, "<li><div>new layout</div></li>")), pause=0, pages=4)
        self.assertEqual(counts["stopped"], "unreadable_page")

    def test_a_short_page_ends_that_query_and_the_allowance_bounds_the_run(self):
        fetch = pages(*[(200, card(200000 + i, "Program Manager")) for i in range(10)])
        _, counts = ls.read(fetch, pause=0, pages=3)
        self.assertEqual(counts["pages"], 3)


class Concert(unittest.TestCase):
    def setUp(self):
        self.db = JobsDB()

    def run_search(self, *bodies, limit=40):
        return ls.run(limit, True, NOW, pages(*bodies), pause=0, connection=self.db)

    def rows(self):
        return self.db.raw.execute("SELECT source, source_url, seen_count, status, location_text FROM v7_jobs ORDER BY id").fetchall()

    def test_a_job_the_alerts_already_have_is_one_row_and_is_not_touched(self):
        url = "https://www.linkedin.com/jobs/view/4463152620"
        self.db.insert("v7_jobs", dedupe_key=identity.url_hash(url), status="NEW", title="Senior TPM", company="Ripple", source="linkedin-alerts",
                       source_url=url, first_seen=NOW, last_seen=NOW, updated_at=NOW, lane="US Remote", seen_count=1)
        counts = self.run_search((200, card(4463152620, "Senior Technical Program Manager", "Ripple") + card(4480548944, "Technical Program Manager", "Ramp")))
        self.assertEqual((counts["new"], counts["already_known"]), (1, 1))
        self.assertEqual(self.rows(), [("linkedin-alerts", url, 1, "NEW", None),
                                       ("linkedin-search", "https://www.linkedin.com/jobs/view/4480548944", 1, "NEW", "United States")])

    def test_an_hourly_repeat_sighting_never_bumps_seen_count(self):
        body = card(4480548944, "Technical Program Manager", "Ramp")
        self.run_search((200, body))
        again = self.run_search((200, body))
        self.assertEqual((again["new"], again["already_known"]), (0, 1))
        self.assertEqual(self.rows()[0][2], 1)                    # repost.is_ghost reads seen_count: a search must not fake a ghost

    def test_cards_are_candidates_never_labelled_remote_and_each_reason_is_counted(self):
        body = (card(300001, "Technical Program Manager", where="United States") + card(300002, "Technical Program Manager", where="Seattle, WA")
                + card(300003, "Staff Software Engineer", where="United States") + card(300004, "Program Manager", where="Remote, US")
                + card(300005, "Program Manager", where="London, England, United Kingdom") + card(300006, "Program Manager", where="Austin, TX (Hybrid)")
                + card(300007, "Delivery Manager", where="Denver, CO (On-site)") + card(300008, "Implementation Manager", where="North America"))
        counts = self.run_search((200, body))
        self.assertEqual(counts["screen"], {"admitted_remote_assumed": 2, "admitted_remote_assumed_place_unresolved": 1, "admitted_remote_in_location": 1,
                                            "non_us": 1, "not_remote_hybrid": 1, "not_remote_onsite": 1, "title_other_profession_engineer": 1})
        self.assertEqual(sorted(r[4] for r in self.rows()), ["North America", "Remote, US", "Seattle, WA", "United States"])   # as given, never "(Remote)"

    def test_cards_that_show_remote_take_the_room_first(self):
        body = card(310001, "Program Manager", where="United States") + card(310002, "Program Manager", where="Remote, US")
        counts = self.run_search((200, body), limit=1)
        self.assertEqual((counts["new"], counts["over_allowance"], [r[4] for r in self.rows()]), (1, 1, ["Remote, US"]))

    def test_new_rows_are_capped_per_run(self):
        body = "".join(card(400000 + i, "Program Manager") for i in range(6))
        counts = self.run_search((200, body), limit=4)
        self.assertEqual((counts["new"], counts["over_allowance"]), (4, 2))

    def test_a_dry_run_writes_nothing(self):
        counts = ls.run(40, False, NOW, pages((200, card(500001, "Program Manager"))), pause=0, connection=self.db)
        self.assertEqual((counts["would_add"], counts["saved"], self.rows()), (1, False, []))


class SharedLane(unittest.TestCase):
    """The LinkedIn resolve lane reads both sources, the alerts first, under one pick (one budget)."""

    def test_the_alerts_come_before_search_rows_and_other_sources_are_not_read(self):
        db = JobsDB()
        for i, source in enumerate(("linkedin-search", "linkedin-alerts", "jobright", "linkedin-search", "linkedin-alerts")):
            url = f"https://www.linkedin.com/jobs/view/{600000 + i}"
            db.insert("v7_jobs", dedupe_key=identity.url_hash(url), status="NEW", title="TPM", company="A", source=source, source_url=url,
                      first_seen=NOW, last_seen=NOW, updated_at=NOW, lane="US Remote", resolve_attempts=0)
        picked = stage.pick_rows(db, "linkedin-alerts", 10)
        sources = [db.raw.execute("SELECT source FROM v7_jobs WHERE id=?", (r[0],)).fetchone()[0] for r in picked]
        self.assertEqual(sources, ["linkedin-alerts", "linkedin-alerts", "linkedin-search", "linkedin-search"])
        self.assertEqual(len(stage.pick_rows(db, "jobright", 10)), 1)


class NoTinyFishForSearchRows(unittest.TestCase):
    """Production-shaped: rows picked from the store by the shared lane carry their source; a search row never reaches the free-search step,
    an alert row still does (that lane's existing behaviour is unchanged)."""

    def test_a_search_row_takes_the_linkedin_page_without_a_search_and_an_alert_row_is_unchanged(self):
        from unittest import mock
        from lifeos.jobs.resolve.aggregators import linkedin
        db = JobsDB()
        for jid, source in ((700001, "linkedin-alerts"), (700002, "linkedin-search")):
            url = f"https://www.linkedin.com/jobs/view/{jid}"
            db.insert("v7_jobs", dedupe_key=identity.url_hash(url), status="NEW", title="TPM", company="Acme", source=source, source_url=url,
                      first_seen=NOW, last_seen=NOW, updated_at=NOW, lane="US Remote", resolve_attempts=0)
        rows = stage.pick_rows(db, "linkedin-alerts", 10)
        self.assertEqual([r[5] for r in rows], ["linkedin-alerts", "linkedin-search"])
        searched = []
        with mock.patch.object(linkedin, "read_guest", return_value={"outcome": "external_hidden"}), \
                mock.patch.object(linkedin.ats_match, "match_many", side_effect=lambda items: ["company"] * len(items)), \
                mock.patch.object(linkedin.search_match, "find", side_effect=lambda c, t: searched.append(c) or ("miss", "no_result")):
            got = linkedin.resolve_rows(rows, budget={"left": 10})
        self.assertEqual(len(searched), 1)                                                          # the alert row only
        self.assertEqual(got[1], {"outcome": "landed", "via": "linkedin_page", "kind": "aggregator", "url": "https://www.linkedin.com/jobs/view/700002"})
