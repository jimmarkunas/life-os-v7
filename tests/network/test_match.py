"""NET-2.2 matching: invented companies, titles and ids only."""
import unittest
from datetime import date

from lifeos import run
from lifeos.network import match, store
from lifeos.network.errors import NetworkError
from lifeos.sources import network_leads
from tests.kit.mysqlite import MySQLite

TODAY = date(2026, 10, 7)


def pos(pid, company, title="Director of Product", state="CURRENT", verified=date(2026, 9, 20)):
    return (pid, company, title, state, verified)


class Leads(unittest.TestCase):
    def test_example_1_current_then_previous_and_an_unrelated_company_is_omitted(self):
        index = match.build_index([pos(1, "acme"), pos(2, "acme", state="SUPERSEDED", verified=date(2026, 8, 1)), pos(3, "widgetco")])
        found = match.leads_for("acme", "Product Director", index, TODAY)
        self.assertEqual([(l["person_id"], l["tier"]) for l in found], [(1, "CURRENT"), (2, "PREVIOUS")])
        self.assertEqual(found[0]["freshness"], "FRESH")
        self.assertEqual(found[1]["freshness"], "AGING")

    def test_example_2_a_stale_lead_is_shown_with_its_age_never_as_current(self):
        index = match.build_index([pos(1, "widgetco", verified=date(2026, 3, 20))])
        found = match.leads_for("widgetco", "Engineer", index, TODAY)
        self.assertEqual((found[0]["freshness"], found[0]["age_days"]), ("STALE", 201))

    def test_example_3_a_company_nobody_lists_yields_nothing(self):
        index = match.build_index([pos(1, "acme")])
        self.assertEqual(match.leads_for("nobodyco", "Engineer", index, TODAY), [])

    def test_a_whole_word_prefix_is_a_possible_match_shown_last_and_a_partial_word_is_not(self):
        index = match.build_index([pos(1, "meta platforms"), pos(2, "meta"), pos(3, "metallica"), pos(4, "metaverse labs")])
        found = match.leads_for("meta", "Engineer", index, TODAY)
        self.assertEqual([(l["person_id"], l["tier"]) for l in found], [(2, "CURRENT"), (1, "POSSIBLE")])      # "metallica" and "metaverse labs" share letters, not a whole word

    def test_order_within_a_tier_is_overlap_then_freshness_then_a_stable_id(self):
        index = match.build_index([pos(1, "acme", "Designer", verified=date(2026, 10, 1)), pos(2, "acme", "Product Manager", verified=date(2026, 8, 1)),
                                   pos(3, "acme", "Product Manager", verified=date(2026, 9, 1)), pos(4, "acme", "Product Manager", verified=date(2026, 9, 1))])
        found = match.leads_for("acme", "Group Product Manager", index, TODAY)
        self.assertEqual([l["person_id"] for l in found], [3, 4, 2, 1])

    def test_at_most_five_and_no_weak_fill(self):
        index = match.build_index([pos(i, "acme") for i in range(1, 9)] + [pos(20 + i, "otherco") for i in range(5)])
        found = match.leads_for("acme", "Engineer", index, TODAY)
        self.assertEqual(len(found), 5)
        self.assertTrue(all(l["person_id"] < 20 for l in found))

    def test_a_person_appears_once_at_their_best_tier(self):
        index = match.build_index([pos(1, "acme", state="SUPERSEDED"), pos(1, "acme"), pos(1, "acme pay")])
        found = match.leads_for("acme", "Engineer", index, TODAY)
        self.assertEqual([(l["person_id"], l["tier"]) for l in found], [(1, "CURRENT")])

    def test_a_blank_company_never_matches(self):
        index = match.build_index([pos(1, "")])
        self.assertEqual(index, {})
        self.assertEqual(match.leads_for("", "Engineer", match.build_index([pos(2, "acme")]), TODAY), [])

    def test_no_closeness_or_willingness_is_ever_in_a_lead(self):
        found = match.leads_for("acme", "Engineer", match.build_index([pos(1, "acme")]), TODAY)
        self.assertEqual(sorted(found[0]), ["age_days", "freshness", "overlap", "person_id", "tier"])


def seeded():
    conn = MySQLite()
    store.ensure_schema(conn)
    conn.raw.execute("CREATE TABLE v7_jobs (id INTEGER PRIMARY KEY, company TEXT, title TEXT, status TEXT, notion_page_id TEXT)")
    conn.raw.execute("CREATE TABLE v7_job_fit (job_id INTEGER, admission TEXT)")
    for pid, key in ((1, "acme"), (2, "acme"), (3, "widgetco")):
        conn.raw.execute("INSERT INTO v7_network_people (id, person_key, url_key, display_name, first_seen, last_observed, status, history_coverage) VALUES (?,?,?,?,?,?,'ACTIVE','SEED_ONLY')",
                         (pid, f"k{pid}", f"person-{pid}", "Invented Person", "2026-09-07 00:00:00", "2026-09-07"))
        conn.raw.execute("INSERT INTO v7_network_positions (person_id, company_key, company_name, title, position_state, first_observed, last_observed, last_verified, source_kind, source_ref,"
                         " source_observed_at, material_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (pid, key, key, "Director", "CURRENT", "2026-09-07", "2026-09-07", "2026-09-20", "X", "r", "2026-09-07", f"h{pid}"))
    jobs = [(1, "Acme Corp", "Product Director", "PUBLISHED", "page-1", "ADMIT"), (2, "Nobody Ltd", "Engineer", "PUBLISHED", "page-2", "ADMIT"),
            (3, "WidgetCo Inc", "Engineer", "PUBLISHED", "page-3", "REVIEW"), (4, "Acme Corp", "Engineer", "READY", None, "ADMIT"), (5, "Widgetco", "Analyst", "PUBLISHED", "page-5", "ADMIT")]
    for jid, company, title, status, page, admission in jobs:
        conn.raw.execute("INSERT INTO v7_jobs VALUES (?,?,?,?,?)", (jid, company, title, status, page))
        conn.raw.execute("INSERT INTO v7_job_fit VALUES (?,?)", (jid, admission))
    return conn


class Stage(unittest.TestCase):
    def test_counts_for_published_admitted_jobs_only_and_nothing_is_written(self):
        conn = seeded()
        before = conn.raw.execute("SELECT (SELECT COUNT(*) FROM v7_network_people), (SELECT COUNT(*) FROM v7_network_positions), (SELECT COUNT(*) FROM v7_jobs)").fetchone()
        counts = network_leads.run(0, True, connection=conn, today=TODAY)
        self.assertEqual((counts["jobs_considered"], counts["jobs_with_leads"], counts["jobs_without_leads"], counts["leads_total"]), (3, 2, 1, 3))
        self.assertEqual(counts["leads_per_job"], {"0": 1, "1": 1, "2": 1, "3": 0, "4": 0, "5": 0})
        self.assertEqual((counts["by_tier"]["CURRENT"], counts["by_freshness"]["FRESH"], counts["live"]), (3, 3, False))
        self.assertEqual(conn.raw.execute("SELECT (SELECT COUNT(*) FROM v7_network_people), (SELECT COUNT(*) FROM v7_network_positions), (SELECT COUNT(*) FROM v7_jobs)").fetchone(), before)
        for text in ("acme", "Acme", "widget", "Director", "page-"):
            self.assertNotIn(text, repr(counts))

    def test_an_empty_roster_is_a_failure_not_zero_leads(self):
        conn = seeded()
        conn.raw.execute("DELETE FROM v7_network_positions")
        with self.assertRaises(NetworkError) as ctx:
            network_leads.run(0, False, connection=conn, today=TODAY)
        self.assertEqual(str(ctx.exception), "NETWORK_NO_ROSTER")

    def test_a_missing_table_fails_with_a_fixed_code(self):
        with self.assertRaises(NetworkError) as ctx:
            network_leads.run(0, False, connection=MySQLite(), today=TODAY)
        self.assertEqual(str(ctx.exception), "NETWORK_MATCH_READ_FAILED")

    def test_the_stage_is_registered_and_creates_no_table(self):
        self.assertEqual(run.STAGES["network-match"].target, ("lifeos.sources.network_leads", "run"))
        self.assertNotIn("CREATE TABLE", open(network_leads.__file__).read())


if __name__ == "__main__":
    unittest.main()
