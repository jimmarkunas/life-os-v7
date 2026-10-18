"""D212: the Ledger read-back reads Notion for every page (one batch property query per KEY_CHUNK keys plus the per-page body marker), and the one-time
recheck reads back exactly the pages D211 verified from a create response. Production SQL over the sqlite kit; only the Notion client is doubled."""
import datetime
import unittest
from unittest import mock

from lifeos.jobs import readback
from lifeos.platform.notion_client import NotionError

T = datetime.datetime(2026, 10, 18, 22, 57, 10)


def props(key, url="https://boards.example.com/a/1", fit=80):
    out = {"Stable Job Key": {"rich_text": [{"plain_text": key}]}, "Apply URL": {"url": url}, "Job": {"title": [{"plain_text": "Product Manager"}]}}
    if fit is not None:
        out["LIFE OS Fit"] = {"number": fit}
    return out


class Ledger:
    """Answers the batched key query, the body-marker GET and nothing else: a page GET is a test failure (the batch query replaced it)."""
    source = "ledger"

    def __init__(self, pages, bodies=None):
        self.pages, self.bodies, self.calls, self.queries = pages, bodies or {}, 0, []

    def call(self, method, path, body=None):
        self.calls += 1
        if method == "POST" and path.endswith("/query"):
            wanted = {f["rich_text"]["equals"] for f in body["filter"]["or"]}
            self.queries.append(len(wanted))
            return {"results": [{"id": pid, "properties": p} for pid, p in self.pages.items()
                                if p["Stable Job Key"]["rich_text"][0]["plain_text"] in wanted], "has_more": False}
        if method == "GET" and "/children" in path:
            pid = path.split("/blocks/")[1].split("/children")[0]
            text = self.bodies.get(pid, "v7-jd:1 | key=x")
            return {"results": [{"paragraph": {"rich_text": [{"plain_text": text}]}}, {"paragraph": {"rich_text": [{"plain_text": "Summary"}]}}]}
        raise AssertionError(f"unexpected Notion call {method} {path}")


class ReadbackBase(unittest.TestCase):
    def setUp(self):
        from tests.kit.jobsdb import JobsDB
        self.db = JobsDB()
        self.addCleanup(self.db.raw.close)

    def published(self, job_id, page, updated=T, verified=None, key=None):
        self.db.insert("v7_jobs", id=job_id, dedupe_key=key or f"k{job_id}", status="PUBLISHED", title="Product Manager", company="Acme",
                       final_apply_url="https://boards.example.com/a/1", first_seen=updated, last_seen=updated, updated_at=updated, source="web",
                       lane="US Remote", notion_page_id=page, verified_at=verified)
        self.db.insert("v7_job_fit", job_id=job_id, score=80, decision="Go", line="fits", confidence="high", model_version="6", profile_hash="p",
                       jd_fingerprint="f", scored_at=updated, admission="ADMIT")

    def verified(self):
        return {row[0]: row[1] for row in self.db.raw.execute("SELECT id, verified_at FROM v7_jobs ORDER BY id").fetchall()}

    def run_with(self, fn, *args, **kwargs):
        with mock.patch.object(readback.store, "connect", return_value=self.db):
            return fn(*args, **kwargs)


class BatchedReadback(ReadbackBase):
    def test_every_page_is_read_from_notion_with_one_query_per_chunk_and_its_own_body_marker(self):
        for i in range(1, 31):
            self.published(i, f"page{i}")
        ledger = Ledger({f"page{i}": props(f"k{i}") for i in range(1, 31)})
        counts = self.run_with(readback.run, ledger, True)
        self.assertEqual((counts["checked"], counts["verified"], counts["failed"]), (30, 30, 0))
        self.assertEqual(ledger.queries, [readback.KEY_CHUNK, 30 - readback.KEY_CHUNK])            # 30 keys: two queries, not 30 page GETs
        self.assertEqual(ledger.calls, 2 + 30)                                                      # plus each page's body-marker read
        self.assertTrue(all(self.verified().values()))

    def test_a_page_the_query_does_not_return_is_gone_and_stays_unverified(self):
        self.published(1, "page1")
        self.published(2, "page2")
        counts = self.run_with(readback.run, Ledger({"page2": props("k2")}), True)
        self.assertEqual((counts["verified"], counts["why"]), (1, {"page_gone": 1}))
        self.assertIsNone(self.verified()[1])

    def test_two_pages_with_one_key_is_ambiguous_never_resolved_by_picking_one(self):
        self.published(1, "page1")
        counts = self.run_with(readback.run, Ledger({"page1": props("k1"), "page9": props("k1")}), True)
        self.assertEqual(counts["why"], {"key_ambiguous": 1})
        self.assertIsNone(self.verified()[1])

    def test_a_wrong_url_or_a_missing_body_marker_fails_the_page(self):
        self.published(1, "page1")
        self.published(2, "page2")
        ledger = Ledger({"page1": props("k1", url="https://wrong.example/x"), "page2": props("k2")}, {"page2": "something else"})
        counts = self.run_with(readback.run, ledger, True)
        self.assertEqual(counts["why"], {"url_mismatch": 1, "no_description": 1})
        self.assertEqual(self.verified(), {1: None, 2: None})

    def test_an_unreadable_query_answer_fails_every_page_and_writes_nothing(self):
        self.published(1, "page1")

        class Broken(Ledger):
            def call(self, method, path, body=None):
                if path.endswith("/query"):
                    return {"results": None}
                return super().call(method, path, body)
        counts = self.run_with(readback.run, Broken({}), True)
        self.assertEqual(counts["why"], {"ledger_lookup_unreadable": 1})
        self.assertIsNone(self.verified()[1])

    def test_a_dry_run_reads_but_writes_nothing(self):
        self.published(1, "page1")
        counts = self.run_with(readback.run, Ledger({"page1": props("k1")}), False)
        self.assertEqual(counts["verified"], 1)
        self.assertIsNone(self.verified()[1])


class CreateVerifiedRecheck(ReadbackBase):
    """Only the pages D211 verified in the same write as their publish, inside proof run 39174107353's publish window, are cleared and read back."""

    def setUp(self):
        super().setUp()
        for i in range(1, 11):                                                    # the 10 the proof run created: verified_at written with the publish
            at = T + datetime.timedelta(seconds=10 * i)
            self.published(i, f"page{i}", updated=at, verified=at)
        earlier = datetime.datetime(2026, 10, 18, 22, 10)
        self.published(11, "page11", updated=earlier, verified=T)                  # one of the 60 the same run verified by reading Notion: untouched
        self.published(12, "page12", updated=T, verified=None)                     # an unverified page: not this correction's
        later = datetime.datetime(2026, 10, 19, 1, 0)
        self.published(13, "page13", updated=later, verified=later)                # outside the window: untouched
        self.before = self.verified()

    def recheck(self, ledger, live=True):
        with mock.patch.object(readback, "Client", return_value=ledger), mock.patch.object(readback.ledger, "verify", return_value=readback.ledger.OK):
            return self.run_with(readback.recheck, 10, live, {})

    def test_exactly_the_ten_are_read_back_and_nothing_else_changes(self):
        ledger = Ledger({f"page{i}": props(f"k{i}") for i in range(1, 14)})
        counts = self.recheck(ledger)
        self.assertEqual((counts["found"], counts["cleared"], counts["failed"]), (10, 10, 0))
        self.assertEqual(counts["readback"]["results"], {str(i): "PASS" for i in range(1, 11)})
        after = self.verified()
        self.assertTrue(all(after[i] and after[i] != self.before[i] for i in range(1, 11)))        # set again, by the read-back
        self.assertEqual({i: after[i] for i in (11, 12, 13)}, {i: self.before[i] for i in (11, 12, 13)})
        self.assertEqual(ledger.queries, [10])                                                       # one query, then ten body reads
        self.assertEqual(ledger.calls, 11)

    def test_a_page_that_does_not_read_back_stays_unverified_and_fails_the_step(self):
        ledger = Ledger({f"page{i}": props(f"k{i}") for i in range(2, 14)})                          # page1 is gone from the Ledger
        counts = self.recheck(ledger)
        self.assertEqual((counts["failed"], counts["readback"]["results"]["1"]), (1, "page_gone"))
        self.assertIsNone(self.verified()[1])

    def test_any_other_count_is_refused_before_a_write(self):
        self.db.raw.execute("UPDATE v7_jobs SET updated_at=? WHERE id=1", (datetime.datetime(2026, 10, 18, 23, 30),))    # a later write moved one: 9 found
        before = self.verified()
        counts = self.recheck(Ledger({}))
        self.assertEqual((counts["found"], counts["refused"], counts["failed"]), (9, "RECHECK_COUNT_MISMATCH", 1))
        self.assertEqual(self.verified(), before)

    def test_a_dry_run_reads_the_ten_but_clears_and_writes_nothing(self):
        counts = self.recheck(Ledger({f"page{i}": props(f"k{i}") for i in range(1, 11)}), live=False)
        self.assertEqual((counts["found"], counts["readback"]["verified"]), (10, 10))
        self.assertNotIn("cleared", counts)
        self.assertEqual(self.verified(), self.before)

    def test_a_live_run_off_the_job_ledger_touches_nothing(self):
        with mock.patch.object(readback, "Client", return_value=Ledger({})), mock.patch.object(readback.ledger, "verify", return_value="wrong_target"):
            counts = self.run_with(readback.recheck, 10, True, {})
        self.assertEqual(counts, {"target": "wrong_target"})
        self.assertEqual(self.verified(), self.before)


class PublishNeverVerifies(unittest.TestCase):
    def test_publish_records_the_fit_it_wrote_but_never_sets_verified_at(self):
        import pathlib
        source = pathlib.Path(readback.__file__).with_name("publish.py").read_text()
        import re
        self.assertIsNone(re.search(r"verified_at\s*=", source))                                     # D212: only the read-back writes verified_at
        self.assertIn("fit_synced_at=%s, meta_v=%s", source)


if __name__ == "__main__":
    unittest.main()
