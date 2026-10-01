"""Newsletter acceptance: mail -> cards -> lane decision -> Ledger payload -> read-back -> finalize (no network, no database)."""
from datetime import date, datetime
import unittest

from lifeos.jobs import lanes, publish, readback
from lifeos.jobs.identity import url_key
from lifeos.sources.newsletters import config, finalize, ingest
from lifeos.sources.newsletters.parsers import lensa
from tests.kit.gmail import FakeGmail

MAIL = ('<a href="https://email.lensa.com/f/a/J1"><table><tr><td>Acme</td></tr><tr><td>Program Manager</td></tr>'
        '<tr><td>Remote</td></tr></table></a>')


class FakeNotion:
    """Echoes back what publish sent, the way the Notion API reads a page."""

    def __init__(self, props, blocks):
        self.props, self.blocks, self.calls = props, blocks, []

    def call(self, method, path, body=None):
        self.calls.append(path)
        if path.startswith("/pages/"):
            def plain(p):
                out = {}
                for k, v in p.items():
                    out[k] = {**v, **{kind: [{"plain_text": t["text"]["content"]} for t in v[kind]] for kind in ("title", "rich_text") if kind in v}}
                return out
            return {"properties": plain(self.props)}
        return {"results": [{"paragraph": {"rich_text": b[b["type"]]["rich_text"] and [{"plain_text": b[b["type"]]["rich_text"][0]["text"]["content"]}]}}
                            if b["type"] == "paragraph" else {"heading_2": {}} for b in self.blocks[:3]]}


class AcceptanceTests(unittest.TestCase):
    def test_a_newsletter_job_reaches_the_ledger_and_reads_back(self):
        card = lensa.parse(MAIL)[0]
        self.assertEqual(ingest.status_for(card, 0), "NEW")
        facts = lanes.facts_for(81, card.title, card.location_text, "Remote role", None, date(2026, 10, 1), date(2026, 10, 1))
        decision, lane = lanes.decide("Newsletter", facts, date(2026, 10, 1))
        self.assertEqual((decision.status, lane), (lanes.ADMIT, "US Remote"))
        key, url = "k1", "https://acme.example/jobs/1"
        row = {"title": card.title, "company": card.company, "url": url, "source": "lensa", "provider": "Lensa", "lane": "Newsletter",
               "key": key, "first_seen": date(2026, 10, 1), "posted": date(2026, 10, 1), "fit": 81, "fit_line": "[81%] Go",
               "admission": decision.status, "work_mode": facts.work_mode}
        props = publish.properties(row)
        blocks = publish.body_blocks(key, {"summary": "About", "responsibilities": "Lead delivery"})
        self.assertIsNone(readback.check(FakeNotion(props, blocks), "p1", key, url, 81))
        self.assertEqual(readback.check(FakeNotion(props, blocks), "p1", "other", url, 81), "key_mismatch")
        self.assertEqual(readback.check(FakeNotion(props, blocks), "p1", key, "https://acme.example/jobs/2", 81), "url_mismatch")
        self.assertEqual(readback.check(FakeNotion(props, blocks[:1]), "p1", key, url, 81), "no_description")
        self.assertEqual(url_key(url), url_key(url + "/"))

    def test_a_uk_newsletter_job_is_excluded_by_market(self):
        facts = lanes.facts_for(85, "Program Manager", "London, UK", "Remote", None, date(2026, 10, 1), date(2026, 10, 1))
        self.assertEqual(lanes.decide("Newsletter", facts, date(2026, 10, 1))[0].status, lanes.EXCLUDE)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=()):
        self.args = args

    def fetchall(self):
        return [r for r in self.rows if r[0] in self.args]


class Conn:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return Cursor(self.rows)


class FinalizeTests(unittest.TestCase):
    def run_finalize(self, rows, ids, live=True):
        gmail = FakeGmail(results={config.FINALIZE_QUERY: ids})
        counts = finalize.finalize(gmail, live, 50, lambda: Conn(rows))
        return counts, gmail.calls

    def test_only_complete_mail_closes(self):
        rows = [("done", "PUBLISHED", 0), ("done", "EXCLUDED_STALE", 1), ("open", "READY", 1), ("open", "PUBLISHED", 0),
                ("unread", "PUBLISHED", 1)]
        counts, calls = self.run_finalize(rows, ["done", "open", "unread", "empty"])
        self.assertEqual(calls, [(["done", "empty"], ["LBL"], [])])                 # no saved jobs = nothing in flight
        self.assertEqual((counts["closed"], counts["in_flight"], counts["unverified"]), (2, 1, 1))

    def test_dry_run_changes_nothing(self):
        counts, calls = self.run_finalize([("a", "PUBLISHED", 0)], ["a"], live=False)
        self.assertEqual((calls, counts["closed"], counts["would_close"]), ([], 0, 1))


if __name__ == "__main__":
    unittest.main()
