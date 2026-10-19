"""D212: the Ledger read-back reads Notion for every page (one batch property query per KEY_CHUNK keys plus the per-page body marker), refuses a pagination answer it cannot
follow, and a page that does not read back fails the publish stage. Production SQL over the sqlite kit; only the Notion client is doubled."""
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

    def test_a_missing_or_repeated_pagination_cursor_is_refused_never_read_as_no_page(self):
        self.published(1, "page1")
        for cursor in (None, "c1"):                                              # has_more with no cursor, or the same cursor handed back again
            class Paged(Ledger):
                def call(self, method, path, body=None, cursor=cursor):
                    if path.endswith("/query"):
                        self.calls += 1
                        return {"results": [], "has_more": True, "next_cursor": cursor if body.get("start_cursor") is None or cursor is None else body["start_cursor"]}
                    return super().call(method, path, body)
            counts = self.run_with(readback.run, Paged({}), True)
            self.assertEqual(counts["why"], {"ledger_lookup_unreadable": 1}, cursor)
            self.assertIsNone(self.verified()[1])

    def test_a_second_page_of_results_is_followed(self):
        self.published(1, "page1")

        class TwoPages(Ledger):
            def call(self, method, path, body=None):
                if path.endswith("/query") and not body.get("start_cursor"):
                    self.calls += 1
                    return {"results": [], "has_more": True, "next_cursor": "c1"}
                return super().call(method, path, body)
        counts = self.run_with(readback.run, TwoPages({"page1": props("k1")}), True)
        self.assertEqual(counts["verified"], 1)

    def test_a_dry_run_reads_but_writes_nothing(self):
        self.published(1, "page1")
        counts = self.run_with(readback.run, Ledger({"page1": props("k1")}), False)
        self.assertEqual(counts["verified"], 1)
        self.assertIsNone(self.verified()[1])


class PublishNeverVerifies(unittest.TestCase):
    def test_a_page_that_does_not_read_back_fails_the_publish_stage(self):
        from lifeos.jobs import fit_sync, publish

        class Client:
            calls, source = 0, "ledger"

            def __init__(self, environ=None):
                pass
        with mock.patch.object(publish, "Client", Client), mock.patch.object(publish.ledger, "verify", return_value=publish.ledger.OK), \
                mock.patch.object(publish.store, "connect"), mock.patch.object(publish.store, "ensure_schema"), mock.patch.object(publish, "_seeded", return_value=True), \
                mock.patch.object(publish, "_pick", return_value=([], set())), mock.patch.object(fit_sync, "run", return_value={}), \
                mock.patch.object(readback, "run", return_value={"checked": 2, "verified": 1, "failed": 1, "why": {"page_gone": 1}}):
            counts = publish.run(10, True, {})
        self.assertEqual(counts["failed"], 1)                                                         # run.PARTIAL_FAILURE turns it into PUBLISH_RECORDS_FAILED, exit 1
        from lifeos import run
        self.assertIn("publish", run.PARTIAL_FAILURE)

    def test_publish_records_the_fit_it_wrote_but_never_sets_verified_at(self):
        import pathlib
        source = pathlib.Path(readback.__file__).with_name("publish.py").read_text()
        import re
        self.assertIsNone(re.search(r"verified_at\s*=", source))                                     # D212: only the read-back writes verified_at
        self.assertIn("fit_synced_at=%s, meta_v=%s", source)


if __name__ == "__main__":
    unittest.main()
