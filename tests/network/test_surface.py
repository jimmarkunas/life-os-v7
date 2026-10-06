"""NET-2.3 surfacing: invented people, companies and ids only. The Ledger is a synthetic Notion (tests/kit/ledger_pages.py), the stores run on an in-memory stand-in."""
import contextlib
import io
import json
import pathlib
import unittest
from datetime import date, datetime, timezone

from lifeos.network import store, surface
from lifeos.network.errors import NetworkError
from lifeos.sources import network_surface
from tests.kit.ledger_pages import FakeLedger
from tests.kit.mysqlite import MySQLite
from tests.network.test_match import pipeline_row, save_pipeline

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 7)
KEY1, KEY2, KEY3 = "a" * 64, "b" * 64, "c" * 64


def build(jobs=None, people=None):
    """people: (id, company_key, company_name, title, state, last_verified). jobs: (job_key, page, company, title, admission)."""
    conn = MySQLite()
    store.ensure_schema(conn)
    conn.raw.execute("CREATE TABLE v7_jobs (id INTEGER PRIMARY KEY, dedupe_key TEXT, status TEXT, company TEXT, title TEXT, notion_page_id TEXT)")
    conn.raw.execute("CREATE TABLE v7_job_fit (job_id INTEGER, admission TEXT)")
    for i, (key, page, company, title, admission) in enumerate(jobs or [(KEY1, "page-1", "Acme Corp", "Product Director", "ADMIT")], 1):
        conn.raw.execute("INSERT INTO v7_jobs VALUES (?,?,?,?,?,?)", (i, key, "PUBLISHED", company, title, page))
        conn.raw.execute("INSERT INTO v7_job_fit VALUES (?,?)", (i, admission))
    for n, (pid, ckey, cname, title, state, verified) in enumerate(people or [(1, "acme", "Acme Corp", "Director of Product", "CURRENT", "2026-09-20"), (2, "acme", "Acme Corp", "Engineer", "CURRENT", "2026-09-25")], 1):
        conn.raw.execute("INSERT OR IGNORE INTO v7_network_people (id, person_key, url_key, display_name, first_seen, last_observed, status, history_coverage) VALUES (?,?,?,?,?,?,'ACTIVE','SEED_ONLY')",
                         (pid, f"k{pid}", f"person-{pid:05d}", f"Invented Person{pid}", "2026-09-07 00:00:00", "2026-09-07"))
        conn.raw.execute("INSERT INTO v7_network_positions (person_id, company_key, company_name, title, position_state, first_observed, last_observed, last_verified, source_kind, source_ref,"
                         " source_observed_at, material_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (pid, ckey, cname, title, state, "2026-09-07", "2026-09-07", verified, "X", "r", "2026-09-07", f"h{n}"))
    save_pipeline(conn, [])                                                                  # the accepted Hiring Pipeline snapshot, empty unless a test fills it
    return conn


def ledger_for(*keys_pages):
    ledger = FakeLedger()
    for key, page in keys_pages or ((KEY1, "page-1"),):
        ledger.add_page(page, key)
    return ledger


def go(conn, ledger, live=False, limit=0, **kw):
    return network_surface.run(limit, live, connection=conn, client=ledger, now=NOW, today=TODAY, **kw)


def degraded(conn, ledger, live=False):
    with contextlib.redirect_stdout(io.StringIO()):
        with unittest.TestCase().assertRaises(NetworkError) as ctx:
            go(conn, ledger, live)
    return str(ctx.exception)


class Surface(unittest.TestCase):
    def test_dry_run_reads_but_writes_nothing_and_creates_no_table(self):
        conn, ledger = build(), ledger_for()
        before = ledger.texts("page-1")
        counts = go(conn, ledger)
        self.assertEqual((counts["blocks"], counts["with_leads"], counts["tables_present"], counts["live"]), ({"would_created": 1}, 1, False, False))
        self.assertEqual((ledger.texts("page-1"), ledger.writes), (before, []))
        self.assertEqual(conn.raw.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('v7_network_dismissals','v7_network_aliases')").fetchone()[0], 0)

    def test_live_creates_the_tables_and_one_owned_block_and_a_replay_writes_nothing(self):
        conn, ledger = build(), ledger_for()
        before = ledger.texts("page-1")
        counts = go(conn, ledger, live=True)
        self.assertEqual((counts["blocks"], counts["tables_present"]), ({"created": 1}, True))
        texts = ledger.texts("page-1")
        self.assertEqual(texts[:3], before)                                                  # the description region is untouched and first
        self.assertEqual(texts[3], "Network Leads (2)")
        owned = ledger.owned("page-1")
        self.assertTrue(owned[0]["paragraph"]["rich_text"][0]["plain_text"].startswith("v7-net:1 "))
        self.assertEqual(sum(1 for c in owned if c["type"] == "to_do"), 2)                    # a Dismiss tick per lead, no Same company tick on a certain match
        ledger.writes.clear()
        again = go(conn, ledger, live=True)
        self.assertEqual((again["blocks"], ledger.writes), ({"unchanged": 1}, []))

    def test_a_dismiss_tick_is_persisted_before_the_block_is_replaced_and_hides_only_that_evidence(self):
        conn, ledger = build(), ledger_for()
        go(conn, ledger, live=True)
        ledger.tick("page-1", "Dismiss · 1:")
        counts = go(conn, ledger, live=True)
        self.assertEqual((counts["ticks_dismiss"], counts["blocks"]), (1, {"replaced": 1}))
        self.assertEqual(conn.raw.execute("SELECT job_key, person_id FROM v7_network_dismissals").fetchall(), [(KEY1, 1)])
        self.assertEqual(ledger.texts("page-1")[3], "Network Leads (1)")
        conn.raw.execute("UPDATE v7_network_positions SET title='VP of Product' WHERE person_id=1")           # new evidence for that person
        go(conn, ledger, live=True)
        self.assertEqual(ledger.texts("page-1")[3], "Network Leads (2)")                                      # it returns only because the evidence changed

    def test_a_same_company_tick_becomes_a_learned_alias_that_never_rewrites_the_stored_company(self):
        conn = build(people=[(1, "meta platforms", "Meta Platforms Inc", "Director", "CURRENT", "2026-09-20")], jobs=[(KEY1, "page-1", "Meta", "Director", "ADMIT")])
        ledger = ledger_for()
        go(conn, ledger, live=True)
        owned = ledger.owned("page-1")
        self.assertTrue(any(c["to_do"]["rich_text"][0]["plain_text"].startswith("Same company ·") for c in owned if c["type"] == "to_do"))
        ledger.tick("page-1", "Same company · 1:")
        counts = go(conn, ledger, live=True)
        self.assertEqual(counts["ticks_same_company"], 1)
        self.assertEqual(conn.raw.execute("SELECT alias_key, company_key FROM v7_network_aliases").fetchall(), [("meta platforms", "meta")])
        self.assertEqual(conn.raw.execute("SELECT company_key, company_name FROM v7_network_positions").fetchall(), [("meta platforms", "Meta Platforms Inc")])     # stored identity unchanged
        self.assertFalse(any(c["to_do"]["rich_text"][0]["plain_text"].startswith("Same company ·") for c in ledger.owned("page-1") if c["type"] == "to_do"))

    def test_a_tick_on_evidence_that_has_since_changed_is_not_carried_over(self):
        conn, ledger = build(), ledger_for()
        go(conn, ledger, live=True)
        conn.raw.execute("UPDATE v7_network_positions SET last_verified='2026-10-01' WHERE person_id=1")
        ledger.tick("page-1", "Dismiss · 1:")
        counts = go(conn, ledger, live=True)
        self.assertEqual((counts["ticks_dismiss"], counts["ticks_stale"]), (0, 1))
        self.assertEqual(conn.raw.execute("SELECT COUNT(*) FROM v7_network_dismissals").fetchone()[0], 0)

    def test_no_leads_removes_an_existing_block_and_never_leaves_a_placeholder(self):
        conn, ledger = build(), ledger_for()
        go(conn, ledger, live=True)
        conn.raw.execute("DELETE FROM v7_network_positions WHERE person_id=1")
        conn.raw.execute("UPDATE v7_network_positions SET company_key='otherco' WHERE person_id=2")
        counts = go(conn, ledger, live=True)
        self.assertEqual(counts["blocks"], {"removed": 1})
        self.assertEqual(ledger.texts("page-1"), ["v7-jd:1 | key=" + KEY1, "Description", "Invented job description."])
        self.assertEqual(go(conn, ledger, live=True)["blocks"], {"none": 1})                 # nothing to remove, nothing created

    def test_a_job_that_stops_being_a_target_loses_its_block_and_the_page_budget_serves_targets_first(self):
        jobs = [(KEY1, "page-1", "Acme Corp", "Product Director", "ADMIT"), (KEY2, "page-2", "Other Co", "Engineer", "REVIEW")]
        conn, ledger = build(jobs=jobs), ledger_for((KEY1, "page-1"), (KEY2, "page-2"))
        go(conn, ledger, live=True)
        conn.raw.execute("UPDATE v7_job_fit SET admission='REVIEW' WHERE job_id=1")                                  # no longer admitted, not an active pursuit
        counts = go(conn, ledger, live=True)
        self.assertEqual(counts["blocks"], {"removed": 1, "none": 1})
        self.assertEqual(len(ledger.texts("page-1")), 3)
        conn.raw.execute("UPDATE v7_job_fit SET admission='ADMIT' WHERE job_id=1")
        saved, network_surface.DEFAULT_PAGES = network_surface.DEFAULT_PAGES, 1
        try:
            counts = go(conn, ledger, live=True)
        finally:
            network_surface.DEFAULT_PAGES = saved
        self.assertEqual((counts["blocks"], counts["pages_deferred"]), ({"created": 1}, 1))                         # the target page was served first; the other waits

    def test_a_page_that_is_not_the_canonical_ledger_page_is_never_written_and_the_run_is_degraded(self):
        for flags, why in (({"archived": True}, "page_gone"), ({"source": "f" * 32}, "wrong_data_source"), ({"description": False}, "no_description_marker")):
            conn, ledger = build(), FakeLedger()
            ledger.add_page("page-1", KEY1, **flags)
            self.assertEqual(degraded(conn, ledger, True), "NETWORK_SURFACE_DEGRADED", flags)
            self.assertEqual(ledger.writes, [], flags)
        conn, ledger = build(), FakeLedger()
        ledger.add_page("page-1", "d" * 64)                                                  # the page's Stable Job Key is not the job's dedupe key
        self.assertEqual(degraded(conn, ledger, True), "NETWORK_SURFACE_DEGRADED")
        self.assertEqual(ledger.writes, [])

    def test_a_failed_notion_read_or_readback_is_degraded_never_zero_leads(self):
        conn, ledger = build(), ledger_for()
        ledger.fail_get = True
        self.assertEqual(degraded(conn, ledger, True), "NETWORK_SURFACE_DEGRADED")
        conn, ledger = build(), ledger_for()
        ledger.drop_appends = True                                                           # Notion accepts the write but the block is not there
        self.assertEqual(degraded(conn, ledger, True), "NETWORK_SURFACE_DEGRADED")

    def test_only_admitted_jobs_and_resolvable_pursuits_get_blocks_and_unsafe_pursuits_are_counted(self):
        jobs = [(KEY1, "page-1", "Acme Corp", "Product Director", "ADMIT"), (KEY2, "page-2", "Acme Corp", "Engineer", "REVIEW"), (KEY3, "page-3", "Acme Corp", "Analyst", "REVIEW")]
        conn, ledger = build(jobs=jobs), ledger_for((KEY1, "page-1"), (KEY2, "page-2"), (KEY3, "page-3"))
        save_pipeline(conn, [pipeline_row("Acme Corp", "Engineer", "Hiring-Manager Interview Scheduled", "hiring_manager", "2026-10-09T15:00:00-05:00"),     # resolves to page-2 (REVIEW but a pursuit)
                             pipeline_row("Acme Corp", "Unlisted Role"),                                                                                          # no Ledger page: no safe surface
                             pipeline_row("Elsewhere Ltd", "Engineer")])
        counts = go(conn, ledger, live=True)
        self.assertEqual((ledger.texts("page-1")[3], ledger.texts("page-2")[3]), ("Network Leads (2)", "Network Leads (2)"))
        self.assertEqual(len(ledger.texts("page-3")), 3)                                      # a plain REVIEW job gets nothing
        self.assertEqual((counts["targets_admitted"], counts["targets_pursuit_resolved"], counts["applied_no_safe_surface"], counts["interview_no_safe_surface"]), (1, 1, 2, 0))
        self.assertEqual(counts["pursuit_ambiguous"], 0)

    def test_two_ledger_pages_for_one_pursuit_are_ambiguous_and_write_nothing_for_it(self):
        jobs = [(KEY1, "page-1", "Zed Corp", "Engineer", "REVIEW"), (KEY2, "page-2", "Zed Corp", "Engineer", "REVIEW")]
        conn, ledger = build(jobs=jobs, people=[(1, "zed", "Zed Corp", "Engineer", "CURRENT", "2026-09-20")]), ledger_for((KEY1, "page-1"), (KEY2, "page-2"))
        save_pipeline(conn, [pipeline_row("Zed Corp", "Engineer")])
        counts = go(conn, ledger, live=True)
        self.assertEqual((counts["pursuit_ambiguous"], counts["applied_no_safe_surface"], ledger.writes), (1, 1, []))

    def test_an_unavailable_pursuit_source_is_degraded_not_silent(self):
        conn, ledger = build(), ledger_for()
        conn.raw.execute("DELETE FROM v7_hiring_pipeline")
        self.assertEqual(degraded(conn, ledger, False), "NETWORK_SURFACE_DEGRADED")          # no snapshot row at all

    def test_dry_without_notion_access_counts_and_live_without_it_fails(self):
        conn = build()
        save_pipeline(conn, [pipeline_row("Acme Corp", "Product Director")])
        counts = network_surface.run(0, False, environ={}, connection=conn, now=NOW, today=TODAY)
        self.assertEqual((counts["pages_unread"], counts["with_leads"]), (1, 1))
        with self.assertRaises(NetworkError) as ctx:
            network_surface.run(0, True, environ={}, connection=conn, now=NOW, today=TODAY)
        self.assertEqual(str(ctx.exception), "NETWORK_SURFACE_CONFIG_MISSING")

    def test_counts_hold_no_name_company_title_link_or_key(self):
        conn, ledger = build(), ledger_for()
        save_pipeline(conn, [pipeline_row("Acme Corp", "Product Director")])
        dump = json.dumps(go(conn, ledger, live=True))
        for text in ("Acme", "Invented", "Director", "person-0", KEY1, "page-1", "linkedin"):
            self.assertNotIn(text, dump)

    def test_the_block_is_the_only_thing_written_and_the_other_blocks_keep_their_ids(self):
        conn, ledger = build(), ledger_for()
        ids = [b["id"] for b in ledger.top("page-1")]
        go(conn, ledger, live=True)
        self.assertEqual([b["id"] for b in ledger.top("page-1")][:3], ids)
        self.assertEqual(set(ledger.writes), {"APPEND"})


def owned_count(ledger, page="page-1"):
    return sum(1 for b in ledger.top(page) if b["type"] == "toggle")


class Corrections(unittest.TestCase):
    """PR 205 review: the explicit page limit, a recoverable replacement, decisions validated against every candidate, and exact alias read-back."""

    def test_an_explicit_limit_of_one_reads_and_writes_at_most_one_page(self):
        jobs = [(KEY1, "page-1", "Acme Corp", "Product Director", "ADMIT"), (KEY2, "page-2", "Acme Corp", "Engineer", "ADMIT")]
        conn, ledger = build(jobs=jobs), ledger_for((KEY1, "page-1"), (KEY2, "page-2"))
        counts = go(conn, ledger, live=True, limit=1)
        self.assertEqual((counts["pages_read"], counts["pages_deferred"], ledger.writes), (1, 1, ["APPEND"]))
        self.assertEqual((owned_count(ledger, "page-1"), owned_count(ledger, "page-2")), (1, 0))
        self.assertEqual(go(conn, ledger, limit=5)["pages_read"], 2)                          # a larger explicit limit reads more; DEFAULT_PAGES applies only when none is supplied

    def test_hourly_rotation_reaches_every_page_without_stored_state(self):
        from datetime import timedelta
        jobs = [(KEY1, "page-1", "Acme Corp", "Product Director", "ADMIT"), (KEY2, "page-2", "Acme Corp", "Engineer", "ADMIT"), (KEY3, "page-3", "Acme Corp", "Analyst", "ADMIT"),
                ("e" * 64, "page-4", "Nowhere Ltd", "Engineer", "REVIEW")]
        conn = build(jobs=jobs)
        ledger = ledger_for((KEY1, "page-1"), (KEY2, "page-2"), (KEY3, "page-3"), ("e" * 64, "page-4"))
        for hour in range(4):
            network_surface.run(2, True, connection=conn, client=ledger, now=NOW + timedelta(hours=hour), today=TODAY)
        self.assertEqual([owned_count(ledger, f"page-{n}") for n in (1, 2, 3)], [1, 1, 1])                  # every target with leads was reached within a few two-page runs
        self.assertEqual(owned_count(ledger, "page-4"), 0)

    def test_a_production_sized_ledger_is_covered_within_the_stated_bound_with_no_starvation(self):
        from datetime import timedelta
        n_leads, n_other, n_nontarget, budget = 154, 921, 525, 40                                          # the live shape: 1,075 targets (154 with leads) and 525 other published pages
        surfaced = {f"t{i:04d}": (f"p{i}", "Co", "Role", set()) for i in range(n_leads + n_other)}
        others = [(f"n{i:04d}", f"q{i}", "Co", "Role") for i in range(n_nontarget)]
        with_leads = {f"t{i:04d}" for i in range(n_leads)}
        total = n_leads + n_other + n_nontarget
        bound = -(-total // budget)                                                                        # ceil(1,600 / 40) = 40 hours
        last, worst = {}, 0
        for hour in range(bound * 4):
            picked, candidates = network_surface._queue(surfaced, others, with_leads, budget, NOW + timedelta(hours=hour))
            self.assertEqual((len(picked), candidates), (budget, total))
            for key, *_ in picked:
                worst = max(worst, hour - last.get(key, -1))
                last[key] = hour
        self.assertEqual(len(last), total)                                                                 # no page is starved, cleanup pool included
        self.assertLessEqual(worst, bound + 1)                                                             # and no page waits longer than the stated bound (plus one run of rounding)
        small = network_surface._queue({"a": ("pa", "C", "R", set())}, [], {"a"}, 1, NOW)[0]
        self.assertEqual([k for k, *_ in small], ["a"])                                                    # an explicit limit of one still serves a page
        self.assertEqual(network_surface._queue({}, [], set(), 40, NOW), ([], 0))

    def test_a_failed_append_leaves_the_previously_accepted_block_untouched(self):
        conn, ledger = build(), ledger_for()
        go(conn, ledger, live=True)
        before = ledger.texts("page-1")
        conn.raw.execute("UPDATE v7_network_positions SET title='VP of Product' WHERE person_id=1")        # new evidence: the block must be replaced
        ledger.drop_appends = True
        self.assertEqual(degraded(conn, ledger, True), "NETWORK_SURFACE_DEGRADED")
        self.assertEqual((ledger.texts("page-1"), "DELETE" in ledger.writes), (before, False))              # the old block is still there; nothing was retired

    def test_an_interrupted_retirement_leaves_the_old_block_visible_and_the_retry_converges(self):
        conn, ledger = build(), ledger_for()
        go(conn, ledger, live=True)
        old = ledger.texts("page-1")[3]
        conn.raw.execute("UPDATE v7_network_positions SET title='VP of Product' WHERE person_id=1")
        ledger.fail_delete = True
        self.assertEqual(degraded(conn, ledger, True), "NETWORK_SURFACE_DEGRADED")
        self.assertEqual(owned_count(ledger), 2)                                                              # old (still visible) and new, never zero and never lost
        self.assertEqual(ledger.texts("page-1")[3], old)
        ledger.fail_delete = False
        counts = go(conn, ledger, live=True)
        self.assertEqual((counts["blocks"], owned_count(ledger)), ({"converged": 1}, 1))
        self.assertEqual(go(conn, ledger, live=True)["blocks"], {"unchanged": 1})                           # and it stays converged

    def test_duplicate_owned_blocks_are_never_left_accumulating(self):
        conn, ledger = build(), ledger_for()
        go(conn, ledger, live=True)
        ledger.drop_appends = False
        ledger.call_once("PATCH", "/blocks/page-1/children", {"children": [surface.build_block(network_surface._leads("Acme Corp", "Product Director", *self.state(conn))[0])]})
        self.assertEqual(owned_count(ledger), 2)
        self.assertEqual(go(conn, ledger, live=True)["blocks"], {"converged": 1})
        self.assertEqual(owned_count(ledger), 1)

    def state(self, conn):
        from lifeos.network import match
        roster, jobs, pipeline = network_surface._read(conn, NOW)
        index = match.build_index([(pid, ckey, title, state, network_surface._day(verified), posid) for pid, ckey, title, state, verified, posid, *_ in roster])
        details = {posid: (name, url, cname, title, network_surface._day(verified)) for pid, ckey, title, state, verified, posid, cname, name, url in roster}
        return index, details, TODAY, {}, set(), KEY1

    def test_a_failed_removal_is_degraded_and_the_block_remains_for_the_retry(self):
        conn, ledger = build(), ledger_for()
        go(conn, ledger, live=True)
        conn.raw.execute("DELETE FROM v7_network_positions")
        conn.raw.execute("INSERT INTO v7_network_positions (person_id, company_key, company_name, title, position_state, first_observed, last_observed, last_verified, source_kind, source_ref,"
                         " source_observed_at, material_hash) VALUES (1,'otherco','Other','x','CURRENT','2026-09-07','2026-09-07','2026-09-20','X','r','2026-09-07','zz')")
        ledger.fail_delete = True
        self.assertEqual(degraded(conn, ledger, True), "NETWORK_SURFACE_DEGRADED")
        self.assertEqual(owned_count(ledger), 1)
        ledger.fail_delete = False
        self.assertEqual(go(conn, ledger, live=True)["blocks"], {"removed": 1})

    def test_a_dismissed_lead_pushed_out_of_the_top_five_keeps_its_decision_and_stays_suppressed(self):
        people = [(1, "acme", "Acme Corp", "Director", "CURRENT", "2026-09-20")] + [(10 + i, "acme", "Acme Corp", "Engineer", "CURRENT", "2026-09-01") for i in range(4)]
        conn, ledger = build(people=people), ledger_for()
        go(conn, ledger, live=True)
        self.assertEqual(ledger.texts("page-1")[3], "Network Leads (5)")
        ledger.tick("page-1", "Dismiss · 1:")                                                                 # Jim dismisses person 1
        for i in range(6):                                                                                    # six new people outrank person 1 (better title overlap, fresher)
            conn.raw.execute("INSERT OR IGNORE INTO v7_network_people (id, person_key, url_key, display_name, first_seen, last_observed, status, history_coverage) VALUES (?,?,?,?,?,?,'ACTIVE','SEED_ONLY')",
                             (50 + i, f"k{50 + i}", f"person-{50 + i:05d}", f"Invented Person{50 + i}", "2026-09-07 00:00:00", "2026-09-07"))
            conn.raw.execute("INSERT INTO v7_network_positions (person_id, company_key, company_name, title, position_state, first_observed, last_observed, last_verified, source_kind, source_ref,"
                             " source_observed_at, material_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (50 + i, "acme", "Acme Corp", "Product Director", "CURRENT", "2026-09-07", "2026-09-07", "2026-10-05", "X", "r", "2026-09-07", f"n{i}"))
        counts = go(conn, ledger, live=True)
        self.assertEqual(counts["ticks_dismiss"], 1)                                                           # validated against every candidate, not only the new five
        self.assertEqual(conn.raw.execute("SELECT person_id FROM v7_network_dismissals").fetchall(), [(1,)])
        conn.raw.execute("DELETE FROM v7_network_positions WHERE person_id >= 50")                              # person 1 would rank back in: still suppressed, evidence unchanged
        go(conn, ledger, live=True)
        refs = [c["to_do"]["rich_text"][0]["plain_text"] for c in ledger.owned("page-1") if c["type"] == "to_do"]
        self.assertFalse(any(r.startswith("Dismiss · 1:") for r in refs))

    def test_a_conflicting_alias_confirmation_fails_closed_and_changes_nothing(self):
        conn = build(people=[(1, "meta platforms", "Meta Platforms Inc", "Director", "CURRENT", "2026-09-20")], jobs=[(KEY1, "page-1", "Meta", "Director", "ADMIT")])
        ledger = ledger_for()
        go(conn, ledger, live=True)
        conn.raw.execute("INSERT INTO v7_network_aliases (alias_key, company_key, confirmed_at) VALUES ('meta platforms', 'somethingelse', '2026-10-01 00:00:00')")
        ledger.tick("page-1", "Same company · 1:")
        before = ledger.texts("page-1")
        self.assertEqual(degraded(conn, ledger, True), "NETWORK_SURFACE_DEGRADED")
        self.assertEqual(conn.raw.execute("SELECT alias_key, company_key FROM v7_network_aliases").fetchall(), [("meta platforms", "somethingelse")])
        self.assertEqual(ledger.texts("page-1"), before)                                                       # nothing replaced
        with self.assertRaises(NetworkError) as ctx:
            store.save_decisions(conn, [], [("meta platforms", "meta")])
        self.assertEqual(str(ctx.exception), "NETWORK_ALIAS_CONFLICT")
        store.save_decisions(conn, [], [("meta platforms", "somethingelse")])                                  # an identical confirmation is idempotent


class Contract(unittest.TestCase):
    ROOT = pathlib.Path(__file__).resolve().parents[2]

    def test_surface_code_has_no_mail_or_pipeline_page_writes_and_is_not_in_the_hourly_workflow(self):
        for name in ("lifeos/network/surface.py", "lifeos/sources/network_surface.py"):
            text = (self.ROOT / name).read_text()
            for banned in ("TinyFish", "tinyfish", "sendMail", "/send", "move(", "hiring_pipeline.read", "Hiring Pipeline page"):
                self.assertNotIn(banned, text, name)
        text = (self.ROOT / ".github/workflows/hourly.yml").read_text()
        step = text[text.index("id: network_surface"):]
        step = step[:step.index("      - id: review")]
        self.assertIn("continue-on-error: true", step)                                                       # a Network failure never fails the Jobs run ...
        self.assertIn("--limit 40", step)                                                                    # ... the bound proven in acceptance ...
        self.assertIn("::warning title=Network surface::", step)                                             # ... but it is always explicit warning evidence, never silent
        self.assertNotIn("network_surface", text[text.index("Lane failures fail the run"):])                 # and it is not in the lane-failure list

    def test_the_importer_does_not_create_the_surface_tables(self):
        conn = MySQLite()
        store.ensure_schema(conn)
        self.assertEqual(conn.raw.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('v7_network_dismissals','v7_network_aliases')").fetchone()[0], 0)


class Block(unittest.TestCase):
    def test_reason_templates_are_fixed_and_stale_leads_say_verify_first(self):
        lead = {"person_id": 1, "tier": "CURRENT", "freshness": "STALE", "age_days": 201, "company": "Acme", "title": "Director", "verified": date(2026, 3, 20)}
        self.assertEqual(surface.reason(lead), "Listed at Acme as Director, as of Mar 20 (201 days) · verify first")
        self.assertEqual(surface.reason(dict(lead, tier="PREVIOUS", freshness="FRESH")), "Previously listed at Acme, as of Mar 20 (201 days)")
        self.assertIn("Same company?", surface.reason(dict(lead, tier="POSSIBLE", freshness="FRESH")))
        for word in ("close", "refer", "willing", "influence", "authority", "warm"):
            self.assertNotIn(word, surface.reason(lead).lower())


if __name__ == "__main__":
    unittest.main()
