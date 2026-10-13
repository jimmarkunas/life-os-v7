import datetime
import unittest

from lifeos.jobs import identity, publish
from lifeos.platform import limits, notion_client


class BodyTests(unittest.TestCase):
    def test_contract_order_marker_and_headings(self):
        blocks = publish.body_blocks("k1", {"summary": "About the role", "responsibilities": "",
                                           "requirements": "\u2022 Python\n\u2022 SQL", "qualifications": "BS degree"})
        kinds = [b["type"] for b in blocks]
        self.assertEqual(kinds, ["paragraph", "heading_2", "paragraph", "heading_2", "bulleted_list_item",
                                 "bulleted_list_item", "heading_2", "paragraph"])
        self.assertEqual(blocks[0]["paragraph"]["rich_text"][0]["text"]["content"], "v7-jd:1 | key=k1")
        self.assertEqual([b["heading_2"]["rich_text"][0]["text"]["content"] for b in blocks if b["type"] == "heading_2"],
                         ["Summary", "Requirements", "Qualifications"])

    def test_the_limit_counts_utf16_units_not_python_characters(self):
        text = "a" * 1997 + "\U0001F600" * 3                                      # 2000 characters, 2003 UTF-16 units: one Notion piece would be refused
        parts = [p["text"]["content"] for p in notion_client.rich_text(text)]
        self.assertEqual("".join(parts), text)                                      # nothing lost or reordered
        self.assertTrue(len(parts) == 2 and all(len(c.encode("utf-16-le")) // 2 <= limits.NOTION_MAX_RICH_TEXT_CHARS for c in parts))
        self.assertEqual([len(p["text"]["content"]) for p in notion_client.rich_text("x" * 4500)], [2000, 2000, 500])      # plain text splits as before
        self.assertEqual(notion_client.rich_text(""), [{"type": "text", "text": {"content": ""}}])

    def test_long_text_is_split_under_the_rich_text_limit(self):
        parts = notion_client.rich_text("x" * 4500)
        self.assertEqual([len(p["text"]["content"]) for p in parts], [2000, 2000, 500])
        self.assertTrue(all(len(p["text"]["content"]) <= limits.NOTION_MAX_RICH_TEXT_CHARS for p in parts))


class PropertyTests(unittest.TestCase):
    def test_core_properties_and_no_fit_fields(self):
        row = {"title": "Data Engineer", "company": "Acme", "url": "https://x.example/j/1", "source": "lensa",
               "provider": "Lensa", "lane": "US Remote", "key": "abc", "first_seen": datetime.date(2026, 9, 30), "posted": datetime.date(2026, 9, 28)}
        props = publish.properties(row)
        self.assertEqual(props["Admission Status"]["select"]["name"], "Passed / Review")
        self.assertEqual(props["Source Types"]["multi_select"][0]["name"], "Lensa")
        self.assertEqual(props["Visible Lane"]["select"]["name"], "US Remote")      # the lane comes from the job row
        self.assertEqual(props["Posting Date"]["date"]["start"], "2026-09-28")
        for fit in ("LIFE OS Fit", "Saturn Decision", "Provider Score"):
            self.assertNotIn(fit, props)

    def test_fit_fields_appear_only_when_scored(self):
        row = {"title": "T", "company": "C", "url": "https://x.example/j/1", "source": "lensa", "provider": "Lensa",
               "lane": "Newsletter", "key": "k", "first_seen": datetime.date(2026, 9, 30), "posted": None}
        props = publish.properties({**row, "fit": 81, "fit_line": "[81%] Go | Strengths: a | Gaps: none"})
        self.assertEqual(props["LIFE OS Fit"]["number"], 81)
        self.assertTrue(props["Why It Fits"]["rich_text"][0]["text"]["content"].startswith("[81%] Go"))

    def test_lane_admission_work_mode_and_pay_are_written(self):
        row = {"title": "T", "company": "C", "url": "https://x.example/j/1", "source": "lensa", "provider": "Lensa",
               "lane": "Newsletter", "key": "k", "first_seen": datetime.date(2026, 9, 30), "posted": None, "fit": 80,
               "fit_line": "[80%] Go", "admission": "REVIEW", "admission_reason": "work mode unresolved",
               "work_mode": "unknown", "salary": "$100K/yr"}
        props = publish.properties(row)
        self.assertEqual(props["Visible Lane"]["select"]["name"], "US Remote")        # a newsletter job is a US Remote job
        self.assertEqual(props["Admission Status"]["select"]["name"], "Passed / Review")
        self.assertEqual(props["Review Reason"]["rich_text"][0]["text"]["content"], "work mode unresolved")
        self.assertEqual(props["Work Mode"]["select"]["name"], "Unknown")
        self.assertEqual(props["Fit Authority"]["select"]["name"], "Authoritative")
        admitted = publish.properties({**row, "admission": "ADMIT", "work_mode": "remote"})
        self.assertEqual(admitted["Admission Status"]["select"]["name"], "Admitted")
        self.assertNotIn("Review Reason", admitted)

    def test_easy_apply_is_flagged_in_source_types(self):
        row = {"title": "T", "company": "C", "url": "https://www.linkedin.com/jobs/view/1", "source": "linkedin", "provider": "LinkedIn",
               "lane": "Newsletter", "key": "k", "first_seen": datetime.date(2026, 9, 30), "posted": None, "apply_kind": "easy_apply"}
        names = [o["name"] for o in publish.properties(row)["Source Types"]["multi_select"]]
        self.assertEqual(names, ["LinkedIn", "Easy Apply"])
        plain = publish.properties({**row, "apply_kind": "ats"})["Source Types"]["multi_select"]
        self.assertEqual([o["name"] for o in plain], ["LinkedIn"])
        agg = publish.properties({**row, "apply_kind": "aggregator"})["Source Types"]["multi_select"]
        self.assertEqual([o["name"] for o in agg], ["LinkedIn", "Aggregator Link"])                # D3 rank 4: flagged, never passed off as the employer's link

    def test_url_key_ignores_query_case_and_trailing_slash(self):
        self.assertEqual(identity.url_key("https://Boards.Greenhouse.io/a/jobs/1/?gh_src=x"),
                         identity.url_key("https://boards.greenhouse.io/a/jobs/1"))


if __name__ == "__main__":
    unittest.main()


class VisibleLaneLabel(unittest.TestCase):
    def test_scale_up_uses_the_ledgers_existing_option_spelling(self):
        row = {"title": "T", "company": "C", "url": "https://x.example/j/1", "source": "web", "provider": "Ashby", "lane": "Scale-Up",
               "key": "k", "first_seen": datetime.date(2026, 10, 1), "posted": None}
        props = publish.properties(row)
        self.assertEqual(props["Visible Lane"]["select"]["name"], "Scale-up")          # not a new "Scale-Up" option
        self.assertEqual(props["Eligible Lanes"]["multi_select"], [{"name": "Scale-Up"}])  # Eligible Lanes' own option is "Scale-Up"


class SameOpeningTests(unittest.TestCase):
    def test_a_published_page_with_the_same_company_and_title_makes_a_duplicate_and_generic_titles_are_never_merged(self):
        from tests.kit.db import FakeCursor
        cursor = FakeCursor(script={"SELECT id FROM v7_jobs WHERE status='PUBLISHED'": (7,)})
        self.assertEqual(publish.same_opening(cursor, 9, "ServiceNow", "Director, TA Infrastructure"), 7)
        self.assertIsNone(publish.same_opening(cursor, 9, "Adobe", "Product Manager"))             # two words: generic
        self.assertIsNone(publish.same_opening(cursor, 9, "", "Director, TA Infrastructure"))
        self.assertEqual(len(cursor.sql), 1)


class LedgerReseedTests(unittest.TestCase):
    """D112: the remembered Ledger URLs were taken before the 9/30 purge, so every archived role stayed 'in_ledger' for good."""

    class Cur:
        def __init__(self, held, known):
            self.held, self.known, self.sql, self.next, self.count = held, known, [], [], 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=()):
            sql % tuple("x" for _ in params)                       # survives driver formatting
            self.sql.append((sql, params))
            self.next = (self.held if "final_apply_url" in sql and sql.startswith("SELECT") else [(h,) for h in self.known] if "SELECT url_hash" in sql else [(1 if self.count else 0,)])

        def executemany(self, sql, rows):
            self.sql.append((sql, list(rows)))

        def fetchall(self):
            return self.next

        def fetchone(self):
            return self.next[0]

    class Conn:
        def __init__(self, cur):
            self.cur = cur

        def cursor(self):
            return self.cur

    def test_the_seed_is_replaced_and_only_jobs_no_longer_in_the_ledger_are_released(self):
        from lifeos.jobs import publish
        from lifeos.jobs.identity import url_key
        archived, live = "https://bluestonex.com/sap-project-manager/", "https://x.example/still-on-the-board"
        cur = self.Cur([(1, archived), (2, live)], [url_key(live)])        # the new seed holds only what is in the Ledger now
        released = publish._store_seed(self.Conn(cur), {url_key(live)})
        self.assertEqual(released, 1)
        statements = [s for s, _ in cur.sql]
        self.assertTrue(statements[0].startswith("DELETE FROM v7_ledger_urls WHERE source='seed'"))
        updates = [p for s, p in cur.sql if s.startswith("UPDATE v7_jobs SET status='READY'")]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][1], 1)

    def test_the_new_marker_is_what_decides_whether_the_ledger_is_read_again(self):
        from lifeos.jobs import publish
        cur = self.Cur([], [])
        self.assertFalse(publish._seeded(self.Conn(cur)))
        self.assertEqual(cur.sql[0][1], (publish.SEED_MARKER,))
        self.assertNotEqual(publish.SEED_MARKER, "0" * 64)


class PublicationOrderTests(unittest.TestCase):
    """D186: which READY jobs a run publishes first, proved on the production selection SQL (sqlite in MySQL dialect)."""

    NOW = datetime.datetime(2026, 10, 13, 9, 0)

    def setUp(self):
        from tests.kit.jobsdb import JobsDB
        self.db = JobsDB()
        self.addCleanup(self.db.raw.close)

    def ready(self, job_id, score, admission="ADMIT", age_days=1, posted_days=None, page=None, described=True):
        first = self.NOW - datetime.timedelta(days=age_days)
        self.db.insert("v7_jobs", id=job_id, dedupe_key=f"k{job_id}", status="READY", title="Product Manager", company="Acme", final_apply_url=f"https://boards.example.com/a/{job_id}",
                       first_seen=first, last_seen=first, updated_at=first, posted_date=(self.NOW - datetime.timedelta(days=posted_days)).date() if posted_days is not None else None,
                       source="web", lane="US Remote", notion_page_id=page)
        self.db.insert("v7_job_fit", job_id=job_id, score=score, decision="Go", line="fits", confidence="high", model_version="v3", profile_hash="p", jd_fingerprint="f",
                       scored_at=first, admission=admission)
        if described:
            self.db.insert("v7_job_descriptions", job_id=job_id, source_kind="ats", full_text="A real description.", fingerprint="f", fetched_at=first)

    def pick(self, n, gated=True, hour=1):
        from lifeos.jobs import publish
        rows, known = publish._pick(self.db, n, gated, hour)             # hour 1: a run with no reserved place below four, so the strongest-first line is what is asserted
        return [row[0] for row in rows]

    def publish(self, ids):
        for job_id in ids:
            self.db.raw.execute("UPDATE v7_jobs SET status='PUBLISHED', notion_page_id=? WHERE id=?", (f"page-{job_id}", job_id))

    def test_a_high_value_new_job_is_served_ahead_of_a_lower_value_older_one(self):
        self.ready(1, 70, age_days=9)                          # older, lower value
        self.ready(2, 91, age_days=0)                          # new, high value
        self.ready(3, 80, age_days=5)
        self.assertEqual(self.pick(1), [2])
        self.assertEqual(self.pick(3), [2, 3, 1])              # the whole line, strongest first

    def test_review_never_outranks_an_admitted_job_and_missing_evidence_never_lifts_a_job(self):
        self.ready(1, 68, age_days=3)                                          # admitted, ordinary
        self.ready(2, None, admission="REVIEW", age_days=0)                    # review: no score, no posting date
        self.ready(3, 95, admission="REVIEW", age_days=0)                      # review with a high score still waits behind the admitted
        self.ready(4, 68, age_days=3, posted_days=None)                        # same score and date as job 1: the tie goes to the lower id
        self.assertEqual(self.pick(4), [1, 4, 3, 2])

    def test_a_newer_posting_wins_among_equal_scores_and_ties_are_deterministic(self):
        self.ready(1, 80, age_days=6, posted_days=6)
        self.ready(2, 80, age_days=6, posted_days=1)                           # same score, fresher posting
        self.ready(3, 80, age_days=6, posted_days=1)                           # identical to 2: first surfaced, then id
        self.assertEqual(self.pick(3), [2, 3, 1])
        self.assertEqual(self.pick(3), self.pick(3))

    def test_hard_exclusions_not_ready_published_and_unread_jobs_are_never_selected(self):
        self.ready(1, 99, admission="EXCLUDE")                                 # the gate's hard exclusion stays absolute whatever the score
        self.ready(2, 99, page="page-2")                                       # already has its page
        self.ready(3, 99, described=False)                                     # no JD
        self.ready(4, 60, admission="REVIEW")
        self.db.raw.execute("UPDATE v7_jobs SET status='HOLD' WHERE id=4")      # not READY
        self.ready(5, 70)
        self.assertEqual(self.pick(10), [5])
        self.assertEqual(self.pick(0), [])

    def test_an_older_valid_job_is_eventually_served_while_new_strong_jobs_keep_arriving(self):
        n = 4                                                                  # one place in four is reserved for the oldest waiting job
        for job_id in range(1, 6):
            self.ready(job_id, 69, age_days=20 - job_id)                       # five old, low-value, valid jobs
        served, next_id = {}, 10
        for run in range(1, 8):
            for _ in range(n):                                                 # a stream of new strong jobs, enough to fill every run by value alone
                self.ready(next_id, 95, age_days=0)
                next_id += 1
            ids = self.pick(n)
            self.assertEqual(len(ids), n)
            self.publish(ids)
            served.update({job_id: run for job_id in ids if job_id < 6})
        self.assertEqual(sorted(served), [1, 2, 3, 4, 5])                      # every old job was served ...
        self.assertEqual(served, {1: 1, 2: 2, 3: 3, 4: 4, 5: 5})              # ... one per run, oldest first, while the strong new jobs still took the other places

    def test_the_order_holds_for_a_large_backlog(self):
        rows = [(i, "ADMIT" if i % 3 else "REVIEW", 60 + i % 40, None, datetime.datetime(2026, 9, 1) + datetime.timedelta(minutes=i)) for i in range(1, 20001)]
        chosen = publish.order(rows, 60)
        self.assertEqual((len(chosen), len(set(chosen))), (60, 60))
        self.assertEqual(chosen, publish.order(list(reversed(rows)), 60))          # the same line whatever order the store returns the rows in
        self.assertEqual(chosen[45:], list(range(1, 16)))                          # the reserved places are the 15 oldest of the rest, oldest first

    def test_small_runs_reserve_a_place_for_the_oldest_one_run_in_four_and_the_normal_line_is_unchanged(self):
        """Limits 1, 2 and 3 have no whole quarter. Sustained strong arrivals (n new, high-ranked jobs before every run) must still let each older valid job through."""
        for n in (1, 2, 3):
            self.db.raw.close()
            self.setUp()
            old = list(range(1, 6))
            for job_id in old:
                self.ready(job_id, 69, age_days=30 - job_id)                       # five valid, low-value, old jobs
            served, next_id = {}, 100
            for run in range(1, 41):
                for _ in range(n):
                    self.ready(next_id, 95, age_days=0)
                    next_id += 1
                ids = self.pick(n, hour=run)                                       # hourly runs on consecutive clock hours
                self.assertEqual(len(ids), n)
                self.publish(ids)
                served.update({job_id: run for job_id in ids if job_id in old and job_id not in served})
                if run % 4:                                                       # no reserved place: the n strongest are published
                    self.assertTrue(all(job_id >= 100 for job_id in ids), (n, run, ids))
            self.assertEqual(sorted(served), old, n)                               # every old job was served ...
            self.assertEqual([served[j] for j in old], [4, 8, 12, 16, 20], n)      # ... oldest first, one per four runs, and within 20 hourly runs
            self.assertEqual(self.db.raw.execute("SELECT COUNT(*) FROM v7_jobs WHERE status='READY' AND id<6").fetchone()[0], 0)
        rows = [(i, "ADMIT" if i % 3 else "REVIEW", 60 + i % 40, None, datetime.datetime(2026, 9, 1) + datetime.timedelta(minutes=i)) for i in range(1, 400)]
        for n in (4, 5, 8, 15, 60):                                               # four places or more: the quarter is unchanged and the hour has no effect
            self.assertEqual(len({tuple(publish.order(rows, n, hour)) for hour in range(8)}), 1, n)
        self.assertEqual(publish.order(rows, 60)[45:], list(range(1, 16)))
        self.assertEqual(publish.order(rows, 0, hour=0), [])                       # a zero-place run publishes nothing, whatever the hour

    def test_the_gate_off_keeps_every_ready_job_in_the_line(self):
        self.ready(1, 80, admission="EXCLUDE")                                 # shadow mode records the decision but does not gate; nothing is dropped here
        self.ready(2, 70)
        self.assertEqual(self.pick(10, gated=False), [2, 1])

    def test_the_real_publish_run_creates_pages_in_priority_order_and_records_each_one(self):
        """The production composition: publish.run -> _pick -> Notion create -> the store writes, over the real SQL. Only the Notion client and the two follow-up stages are doubled."""
        from unittest import mock
        from lifeos.jobs import fit_sync, publish, readback
        self.db.insert("v7_ledger_urls", url_hash=publish.SEED_MARKER, source="seed", seen_at=self.NOW)       # the Ledger was read once: no cold-start read
        self.ready(1, 70, age_days=9)
        self.ready(2, 91, age_days=0)
        self.ready(3, 80, age_days=5, admission="REVIEW")
        created = []

        class Client:
            calls = 0
            source = "ledger"

            def __init__(self, environ=None):
                pass

            def create(self, props, blocks):
                created.append(props["Stable Job Key"]["rich_text"][0]["text"]["content"])
                return "page-" + created[-1]

        with mock.patch.object(publish, "Client", Client), mock.patch.object(publish.ledger, "verify", return_value=publish.ledger.OK), \
                mock.patch.object(publish.store, "connect", return_value=self.db), mock.patch.object(publish.store, "ensure_schema"), \
                mock.patch.object(fit_sync, "run", return_value={}), mock.patch.object(readback, "run", return_value={}), \
                mock.patch.object(publish, "_now", return_value=datetime.datetime(2026, 10, 13, 9, 0)):
            counts = publish.run(2, True, {"V7_FIT_GATE": "true"})
        self.assertEqual((counts["picked"], counts["created"], counts["failed"]), (2, 2, 0))
        self.assertEqual(created, ["k2", "k1"])                                               # strongest first: the admitted 91, then the admitted 70
        status = dict(self.db.raw.execute("SELECT id, status FROM v7_jobs").fetchall())
        self.assertEqual(status, {1: "PUBLISHED", 2: "PUBLISHED", 3: "READY"})                # the Review job (80) waits behind both admitted jobs
