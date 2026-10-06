"""NET-2.2 matching: invented companies, titles and ids only."""
import json
import pathlib
import unittest
from datetime import date, datetime, timezone

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
        self.assertEqual(sorted(found[0]), ["age_days", "company_key", "freshness", "overlap", "person_id", "tier"])


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


NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def pipeline_row(company, role, stage=None, kind=None, start=None, applied_on="2026-09-30"):
    return {"key": "k", "company": company, "role": role, "applied_on": applied_on, "parent": None, "parent_state": "none", "stage": stage, "event_kind": kind, "event_start": start, "rounds": 0, "carried": False}


def save_pipeline(conn, rows, reasons=()):
    from lifeos.hiring import snapshot
    snap = {"schema": snapshot.SCHEMA_V, "taken_at": NOW.isoformat(), "accepted_at": NOW.isoformat(), "reasons": list(reasons), "rows": rows}
    snapshot.STORE.ensure(conn)                                      # the stand-in has no ON DUPLICATE KEY: write the row the pipeline would have saved
    conn.raw.execute("DELETE FROM v7_hiring_pipeline")
    conn.raw.execute("INSERT INTO v7_hiring_pipeline (snapshot_id, taken_at, schema_v, payload) VALUES (?,?,?,?)", (snapshot.KEY, "2026-10-07 12:00:00", snapshot.SCHEMA_V, json.dumps(snap)))


class Stage(unittest.TestCase):
    def test_admitted_job_counts_only_published_admit_and_nothing_is_written(self):
        conn = seeded()
        before = conn.raw.execute("SELECT (SELECT COUNT(*) FROM v7_network_people), (SELECT COUNT(*) FROM v7_network_positions), (SELECT COUNT(*) FROM v7_jobs)").fetchone()
        counts = network_leads.run(0, True, connection=conn, today=TODAY, now=NOW)
        job = counts["by_class"]["admitted_job"]
        self.assertEqual((job["considered"], job["with_leads"], job["without_leads"], job["leads_total"]), (3, 2, 1, 3))
        self.assertEqual(job["leads_per_target"], {"0": 1, "1": 1, "2": 1, "3": 0, "4": 0, "5": 0})
        self.assertEqual((job["by_tier"]["CURRENT"], job["by_freshness"]["FRESH"], counts["live"], counts["pursuit_source"]), (3, 3, False, "unavailable"))
        self.assertEqual(conn.raw.execute("SELECT (SELECT COUNT(*) FROM v7_network_people), (SELECT COUNT(*) FROM v7_network_positions), (SELECT COUNT(*) FROM v7_jobs)").fetchone(), before)
        for text in ("acme", "Acme", "widget", "Director", "page-"):
            self.assertNotIn(text, repr(counts))

    def test_applied_opportunities_and_upcoming_interviews_are_their_own_trigger_classes(self):
        conn = seeded()
        save_pipeline(conn, [pipeline_row("Acme Corp", "Product Director"),                                                              # applied only
                             pipeline_row("WidgetCo", "Analyst", "Hiring-Manager Interview Scheduled", "hiring_manager", "2026-10-09T15:00:00-05:00"),   # applied and an upcoming interview
                             pipeline_row("Nobody Ltd", "Engineer", "Interviewing", "interview", "2026-09-01T15:00:00-05:00"),                 # a past interview is not upcoming
                             pipeline_row("Acme Corp", "Engineer", "Submitted", None, None, applied_on=None)])                                  # an active-pursuit page
        counts = network_leads.run(0, False, connection=conn, today=TODAY, now=NOW)
        applied, interview = counts["by_class"]["applied"], counts["by_class"]["interview"]
        self.assertEqual((applied["considered"], applied["with_leads"], applied["without_leads"]), (4, 3, 1))
        self.assertEqual((interview["considered"], interview["with_leads"]), (1, 1))                                                     # only the upcoming one
        self.assertEqual((counts["pursuit_source"], counts["by_class"]["admitted_job"]["considered"]), ("ok", 3))
        self.assertGreaterEqual(counts["distinct_with_leads"], 2)
        for text in ("Acme", "WidgetCo", "Analyst", "2026-10-09"):
            self.assertNotIn(text, repr(counts))

    def test_the_hiring_pipelines_own_active_rule_decides_which_applications_are_targets(self):
        conn = seeded()
        save_pipeline(conn, [pipeline_row("Acme Corp", "Old", "Submitted", None, None, applied_on="2026-08-01"),                                # older than 30 days: no longer active
                             pipeline_row("Acme Corp", "Edge", "Submitted", None, None, applied_on="2026-09-07"),                               # exactly 30 days: still active
                             pipeline_row("Acme Corp", "Fresh", "Submitted", None, None, applied_on="2026-10-01"),
                             pipeline_row("Acme Corp", "Undated", "Submitted", None, None, applied_on=None),                                    # an undated one stays
                             dict(pipeline_row("Acme Corp", "Carried", "Submitted", None, None, applied_on="2026-07-01"), carried=True),        # carried safe state keeps its behaviour
                             pipeline_row("Acme Corp", "Later", "Interviewing", "interview", "2026-09-01T10:00:00-05:00", applied_on="2026-07-01")])   # a later stage stays
        counts = network_leads.run(0, False, connection=conn, today=TODAY, now=NOW)
        self.assertEqual((counts["by_class"]["applied"]["considered"], counts["older_submitted_excluded"]), (5, 1))
        from lifeos.hiring import render as hiring_render
        row = pipeline_row("Acme Corp", "Old", "Submitted", None, None, applied_on="2026-08-01")
        self.assertTrue(hiring_render.older_submitted(row, NOW) and network_leads.hiring_render.older_submitted is hiring_render.older_submitted)      # one rule, shared

    def test_an_unreadable_or_degraded_pipeline_is_reported_never_a_silent_zero(self):
        conn = seeded()
        self.assertEqual(network_leads.run(0, False, connection=conn, today=TODAY, now=NOW)["pursuit_source"], "unavailable")             # no snapshot row at all
        save_pipeline(conn, [pipeline_row("Acme Corp", "Engineer")], reasons=["CALENDAR_UNAVAILABLE"])
        self.assertEqual(network_leads.run(0, False, connection=conn, today=TODAY, now=NOW)["pursuit_source"], "carried")

    def test_a_pipeline_row_without_a_company_is_ignored_and_a_company_with_no_key_is_counted(self):
        conn = seeded()
        save_pipeline(conn, [pipeline_row("", "Engineer"), pipeline_row("Inc.", "Engineer")])
        applied = network_leads.run(0, False, connection=conn, today=TODAY, now=NOW)["by_class"]["applied"]
        self.assertEqual((applied["considered"], applied["without_company_key"]), (1, 1))

    def test_an_empty_roster_is_a_failure_not_zero_leads(self):
        conn = seeded()
        conn.raw.execute("DELETE FROM v7_network_positions")
        with self.assertRaises(NetworkError) as ctx:
            network_leads.run(0, False, connection=conn, today=TODAY, now=NOW)
        self.assertEqual(str(ctx.exception), "NETWORK_NO_ROSTER")

    def test_a_missing_table_fails_with_a_fixed_code(self):
        with self.assertRaises(NetworkError) as ctx:
            network_leads.run(0, False, connection=MySQLite(), today=TODAY, now=NOW)
        self.assertEqual(str(ctx.exception), "NETWORK_MATCH_READ_FAILED")

    def test_the_stage_is_registered_reads_only_and_creates_no_table(self):
        self.assertEqual(run.STAGES["network-match"].target, ("lifeos.sources.network_leads", "run"))
        text = pathlib.Path(network_leads.__file__).read_text()
        for banned in ("CREATE TABLE", "INSERT", "UPDATE", "DELETE", "snapshot.save", "call_once", "Client("):
            self.assertNotIn(banned, text)


if __name__ == "__main__":
    unittest.main()
