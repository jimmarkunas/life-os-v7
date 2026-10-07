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


class PickOrderTests(unittest.TestCase):
    """JOBS-1.1A: the per-run cap takes the newest actionable jobs first: Posting Date when supported, else First Surfaced, First Surfaced breaking ties."""

    class Conn:
        def __init__(self, db):
            self.db = db

        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=()):
            self.cur = self.db.execute(sql.replace("%s", "?"), params)

        def fetchall(self):
            return self.cur.fetchall()

        def fetchone(self):
            return self.cur.fetchone()

    def database(self):
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.executescript(
            "CREATE TABLE v7_jobs (id INTEGER PRIMARY KEY, dedupe_key TEXT, title TEXT, company TEXT, final_apply_url TEXT, source TEXT, provider TEXT, lane TEXT, first_seen TEXT,"
            " posted_date TEXT, salary_text TEXT, apply_kind TEXT, location_text TEXT, route_evidence TEXT, status TEXT, notion_page_id TEXT);"
            "CREATE TABLE v7_job_descriptions (job_id INT, summary TEXT, responsibilities TEXT, requirements TEXT, qualifications TEXT, full_text TEXT);"
            "CREATE TABLE v7_job_fit (job_id INT, score INT, line TEXT, admission TEXT, admission_reason TEXT, work_mode TEXT, lane TEXT, eligible TEXT);"
            "CREATE TABLE v7_ledger_urls (url_hash TEXT);")
        for job_id, first_seen, posted in ((1, "2026-09-01 08:00:00", None), (2, "2026-10-05 08:00:00", "2026-10-04"), (3, "2026-10-06 08:00:00", None), (4, "2026-09-02 08:00:00", "2026-10-06")):
            db.execute("INSERT INTO v7_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'READY',NULL)", (job_id, f"k{job_id}", "PM", "Acme", f"https://acme.example/{job_id}", "s", "p", "US Remote", first_seen, posted, None, None, None, None))
            db.execute("INSERT INTO v7_job_descriptions VALUES (?,?,?,?,?,?)", (job_id, "s", "", "", "", "t"))
        return db

    def test_newest_posting_or_first_surfaced_comes_first_and_the_cap_never_starves_current_jobs(self):
        conn = self.Conn(self.database())
        self.assertEqual([row[0] for row in publish._pick(conn, 60)[0]], [3, 4, 2, 1])        # 10-06 surfaced, 10-06 posted (surfaced earlier), 10-04 posted, 09-01 oldest
        self.assertEqual([row[0] for row in publish._pick(conn, 2)[0]], [3, 4])               # a cap of two keeps the two newest, not the two oldest
