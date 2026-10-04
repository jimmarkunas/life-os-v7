"""D123: the title watch finds roles a company's own site lists but its board listing does not carry."""
import unittest
from datetime import date, datetime
from unittest.mock import patch

from lifeos.jobs import audit, lanes
from lifeos.platform.tinyfish import TinyFishError
from lifeos.sources.web import registry, titlewatch as tw
from tests.kit.db import FakeConn

NOW = datetime(2026, 10, 4, 20, 0)
CRYPTO = "https://www.revolut.com/careers/position/c7078b70-e10b-4f47-b983-bbe6d08d098a/"
CREATIVE = "https://www.revolut.com/careers/position/campaign-creative-lead-92db1b5f-56bc-4524-bc8b-5f1d2188ae1a/"
SOURCE = {"id": "su-revolut-ltd", "company": "Revolut Ltd", "title_watch": {"domains": ["revolut.com"], "path": "/careers/position/"}}


def hit(url, title, snippet=""):
    return {"url": url, "title": title, "snippet": snippet}


class ParsingTests(unittest.TestCase):
    def test_role_title_is_the_part_that_names_a_watched_title(self):
        for raw in ("Product Owner (Crypto) | Revolut", "Revolut - Careers: Product Owner (Crypto)", "Product Owner (Crypto) - Revolut"):
            self.assertEqual(tw.role_title(raw, tw.DEFAULT_TITLES), "Product Owner (Crypto)", raw)
        self.assertEqual(tw.role_title("Join Revolut | Careers", tw.DEFAULT_TITLES), "")
        self.assertEqual(tw.role_title("Campaign Creative Lead - Revolut", tw.DEFAULT_TITLES), "Campaign Creative Lead")

    def test_only_job_pages_on_the_companys_own_domain_with_a_watched_title_are_candidates(self):
        results = [hit(CRYPTO, "Product Owner (Crypto) | Revolut", "London"), hit(CREATIVE, "Campaign Creative Lead - Revolut"),
                   hit("https://www.glassdoor.com/job/product-owner-revolut-123456", "Product Owner"),
                   hit("https://evil-revolut.com/careers/position/product-owner-aaaaaaaa-1/", "Product Owner"),
                   hit("https://www.revolut.com/about/", "Product Owner"),
                   hit("https://www.revolut.com/careers/position/data-engineer-11111111-1111-1111-1111-111111111111/", "Data Engineer | Revolut"),
                   hit("http://www.revolut.com/careers/position/product-owner-22222222-2222-2222-2222-222222222222/", "Product Owner")]
        self.assertEqual([c[0] for c in tw.candidates(SOURCE, results)], [CRYPTO, CREATIVE])

    def test_token_is_the_uuid_or_the_last_path_segment(self):
        self.assertEqual(tw.token(CREATIVE), "92db1b5f-56bc-4524-bc8b-5f1d2188ae1a")
        self.assertEqual(tw.token("https://truvi.com/careers/product-manager-london"), "product-manager-london")

    def test_the_rotation_covers_every_pair_within_the_budget_and_never_repeats_in_one_run(self):
        sources = registry.load(registry.PATHS["Scale-Up"])
        pairs = tw.pairs(sources)
        self.assertGreater(len(pairs), tw.MAX_QUERIES_PER_RUN)
        seen = set()
        for hour in range(len(pairs)):
            window = tw.window(pairs, datetime(2026, 10, 4, 0, 0).replace(hour=hour % 24, day=4 + hour // 24))
            self.assertEqual(len(window), tw.MAX_QUERIES_PER_RUN)
            self.assertEqual(len({(s["id"], t) for s, t in window}), tw.MAX_QUERIES_PER_RUN)
            seen |= {(s["id"], t) for s, t in window}
        self.assertEqual(len(seen), len(pairs))

    def test_a_dispatch_naming_a_company_searches_all_its_titles_not_just_the_hours_slice(self):
        pairs = tw.pairs(registry.load(registry.PATHS["Scale-Up"]))
        got = tw.forced(pairs, "revolut")
        self.assertEqual({s["id"] for s, _ in got}, {"su-revolut-ltd"})
        self.assertEqual(len(got), len(tw.DEFAULT_TITLES))
        self.assertEqual(tw.forced(pairs, "board:Revolut, truvi").__len__(), 2 * len(tw.DEFAULT_TITLES))
        self.assertEqual(tw.forced(pairs, ""), [])
        self.assertEqual(tw.forced(pairs, "funnel"), [])
        self.assertEqual(tw.forced(pairs, "ab"), [])                                            # names need 3 letters

    def test_the_registry_watches_revolut_and_the_own_site_fallback_companies_only(self):
        watched = {s["id"] for s in tw.watched(registry.load(registry.PATHS["Scale-Up"]))}
        self.assertIn("su-revolut-ltd", watched)
        for sid in watched:
            self.assertNotIn("linkedin", next(s for s in registry.load(registry.PATHS["Scale-Up"]) if s["id"] == sid)["url"])


class RunTests(unittest.TestCase):
    def fake_search(self, results, fail=False):
        def search(query, include_domains=None):
            if fail:
                raise TinyFishError("TINYFISH_SEARCH_HTTP_500")
            self.queries.append((query, tuple(include_domains)))
            return results
        return search

    def setUp(self):
        self.queries = []

    def run_watch(self, results, known=(), live=True, **kw):
        added, closed = [], []
        conn = FakeConn()

        def stored(cursor, url):
            return tw.token(url) in known

        with patch.object(tw, "_stored", stored), patch.object(tw, "store") as store, \
                patch.object(tw, "admit", lambda connection, source, url, title, snippet, now: added.append((url, title, snippet)) or True), \
                patch.object(tw, "expire", lambda connection, now: closed.append(1) or 2):
            store.connect = lambda: conn
            counts = tw.run(0, live, search=self.fake_search(results, kw.get("fail", False)), connect=lambda: conn, sources=[SOURCE], now=NOW)
        return counts, added, closed

    def test_a_hidden_role_is_found_known_roles_are_skipped_and_new_ones_added(self):
        counts, added, closed = self.run_watch([hit(CRYPTO, "Product Owner (Crypto) | Revolut", "London product role"), hit(CREATIVE, "Campaign Creative Lead")],
                                               known={tw.token(CREATIVE)})
        self.assertEqual((counts["known"], counts["new"], counts["added"], counts["expired"]), (1, 1, 1, 2))
        self.assertEqual(added, [(CRYPTO, "Product Owner (Crypto)", "London product role")])
        self.assertTrue(all(q[1] == ("revolut.com",) for q in self.queries))
        self.assertEqual(counts["queries"], len(tw.DEFAULT_TITLES))

    def test_dry_run_adds_and_closes_nothing(self):
        counts, added, closed = self.run_watch([hit(CRYPTO, "Product Owner (Crypto)")], live=False)
        self.assertEqual((counts["new"], counts["added"], counts["expired"], added, closed), (1, 0, 0, [], []))

    def test_a_search_outage_fails_the_stage_and_a_partial_one_does_not(self):
        with self.assertRaises(TinyFishError):
            self.run_watch([], fail=True)
        calls = []

        def flaky(query, include_domains=None):
            calls.append(query)
            if len(calls) % 2:
                raise TinyFishError("TINYFISH_SEARCH_HTTP_429")
            return []
        with patch.object(tw, "store"):
            counts = tw.run(0, False, search=flaky, connect=lambda: FakeConn(), sources=[SOURCE], now=NOW)
        self.assertGreater(counts["errors"], 0)
        self.assertLess(counts["errors"], counts["queries"])

    def test_the_new_roles_per_run_are_capped(self):
        results = [hit(f"https://www.revolut.com/careers/position/product-owner-{i:08x}-1111-1111-1111-111111111111/", f"Product Owner {i} | Revolut") for i in range(15)]
        counts, added, _ = self.run_watch(results)
        self.assertEqual((counts["new"], counts["added"]), (15, tw.MAX_NEW_PER_RUN))


class AdmitTests(unittest.TestCase):
    def test_the_role_is_stored_as_a_title_only_ready_job_from_its_own_source(self):
        saved = []
        conn = FakeConn(script={"SELECT id FROM v7_jobs WHERE dedupe_key": (7,)})
        with patch.object(tw.intake, "add_job", lambda cursor, job, now: saved.append(job) or ("k", True)), \
                patch.object(tw.enrich, "save", lambda connection, job_id, result: saved.append((job_id, result))):
            self.assertTrue(tw.admit(conn, SOURCE, CREATIVE, "Campaign Creative Lead", "London · brand campaigns", NOW))
        job, (job_id, result) = saved
        self.assertEqual((job["source"], job["lane"], job["url"], job["location"]), ("web:titlewatch:su-revolut-ltd", "Scale-Up", CREATIVE, "London"))
        self.assertEqual((job_id, result["outcome"], result["source_kind"], result["final_url"]), (7, "ready", "title_watch", CREATIVE))
        self.assertIn("Campaign Creative Lead", result["description"]["full_text"])

    def test_a_known_job_is_not_added_again(self):
        conn = FakeConn(script={"SELECT id FROM v7_jobs WHERE dedupe_key": (7,)})
        with patch.object(tw.intake, "add_job", lambda cursor, job, now: ("k", False)), patch.object(tw.enrich, "save") as save:
            self.assertFalse(tw.admit(conn, SOURCE, CREATIVE, "Campaign Creative Lead", "", NOW))
        save.assert_not_called()

    def test_expiry_closes_only_title_watch_jobs_not_seen_for_ten_days(self):
        conn = FakeConn()
        tw.expire(conn, NOW)
        sql, args = conn.cur.sql[0][0], conn.cur.sql[0][1]
        self.assertEqual(args[1], "web:titlewatch:%")
        self.assertEqual((NOW - args[2]).days, tw.EXPIRE_DAYS)


class LaneTests(unittest.TestCase):
    TODAY = date(2026, 10, 4)

    def facts(self, fit, **kw):
        return lanes.facts_for(fit, "Product Owner (Crypto)", kw.pop("location", "London"), "x", None, None, self.TODAY, route="Scale-up:POSITIVE", first_party=True,
                               **kw)

    def decide(self, fit, **kw):
        return lanes.decide("Scale-Up", self.facts(fit, **kw), self.TODAY)[0]

    def test_a_title_only_role_is_never_a_go_whatever_it_scores(self):
        for fit in (95, 70, 68):
            decision = self.decide(fit, title_only=True, kept=True)
            self.assertEqual((decision.status, decision.reason), (lanes.REVIEW, lanes.TITLE_ONLY_REASON), fit)
        self.assertEqual(self.decide(95).status, lanes.ADMIT)                                  # a read posting at the same score is a Go

    def test_a_low_score_or_an_unresolved_place_never_excludes_a_watched_title(self):
        self.assertEqual(self.decide(40, title_only=True, kept=True).status, lanes.REVIEW)
        self.assertEqual(self.decide(80, title_only=True, kept=True, location="").status, lanes.REVIEW)     # D100 does not turn a chosen title into an exclusion
        self.assertEqual(self.decide(40).status, lanes.EXCLUDE)                                # the same score from a read posting is excluded

    def test_a_hard_exclusion_still_wins_for_a_title_watch_role(self):
        decision = lanes.decide("Scale-Up", self.facts(80, title_only=True, kept=True), self.TODAY, "clinical")[0]
        self.assertEqual(decision.status, lanes.EXCLUDE)

    def test_the_source_marker_and_the_audit_exemption(self):
        self.assertTrue(lanes.title_watch_source("web:titlewatch:su-revolut-ltd"))
        self.assertFalse(lanes.title_watch_source("web:su-revolut-ltd"))
        self.assertFalse(lanes.title_watch_source(None))
        self.assertTrue(lanes.first_party_source("web:titlewatch:su-revolut-ltd"))
        url = "https://www.revolut.com/careers/position/campaign-creative-lead-92db1b5f-56bc-4524-bc8b-5f1d2188ae1a/"
        self.assertEqual(audit.judge(url, "short snippet"), "audit_jd_thin")
        self.assertIsNone(audit.judge(url, "short snippet", title_only=True))
        self.assertEqual(audit.judge("https://www.revolut.com/careers/", "short", title_only=True), "audit_url_listing_url")   # the link test still applies


if __name__ == "__main__":
    unittest.main()
