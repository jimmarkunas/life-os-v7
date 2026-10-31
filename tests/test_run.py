"""Every pipeline stage resolves to a real function; the registry is the only place a stage is declared."""
import contextlib
import importlib
import inspect
import io
import json
import unittest
import warnings
from unittest.mock import patch

from lifeos import run


class RegistryTests(unittest.TestCase):
    def test_every_lazy_stage_points_at_a_function_taking_limit_and_live(self):
        lazies = {name: fn for name, fn in run.STAGES.items() if hasattr(fn, "target")}
        self.assertGreater(len(lazies), 15)
        for name, fn in lazies.items():
            module, attr = fn.target
            target = getattr(importlib.import_module(module), attr, None)
            self.assertTrue(callable(target), name)
            self.assertGreaterEqual(len(inspect.signature(target).parameters), 2, name)

    def test_lazy_passes_limit_floor_live_and_fixed_arguments(self):
        calls = []
        fake = type("M", (), {"go": staticmethod(lambda limit, live, **kw: calls.append((limit, live, kw)) or {"n": 1})})
        with patch("importlib.import_module", return_value=fake):
            self.assertEqual(run.lazy("m", "go", floor=500, lane="X")(40, True), {"n": 1})
        self.assertEqual(calls, [(500, True, {"lane": "X"})])

    def test_a_failing_stage_prints_a_fixed_code_and_returns_1(self):
        with patch.dict(run.STAGES, {"probe": lambda limit, live: (_ for _ in ()).throw(run.JiraError("JIRA_NETWORK"))}):
            self.assertEqual(run.main(["probe"]), 1)


class PartialFailureExitTests(unittest.TestCase):
    """D182: a publish run that failed a record exits 1 and still prints its counts; nothing else changes."""

    @staticmethod
    def _main(stage, counts):
        out, err = io.StringIO(), io.StringIO()
        with warnings.catch_warnings(), patch.dict(run.STAGES, {stage: lambda limit, live: counts}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            warnings.simplefilter("ignore", ResourceWarning)                    # another test's unclosed sqlite connection can be collected (and warned about) inside this one's stderr capture
            code = run.main([stage, "--live"])
        return code, out.getvalue(), err.getvalue()

    def test_publish_with_a_failed_record_exits_1_keeps_the_counts_line_and_names_a_fixed_code(self):
        counts = {"picked": 2, "created": 1, "duplicate": 0, "failed": 1, "seeded": 0, "calls": 6, "error": "NOTION_HTTP_400 validation_error body.children[2]",
                  "fit_sync": {"due": 0, "failed": 0, "synced": 0}, "readback": {"checked": 1, "failed": 0, "verified": 1}, "target": "ok"}
        code, out, err = self._main("publish", counts)
        self.assertEqual(code, 1)
        self.assertEqual(out, "publish: " + json.dumps(counts, sort_keys=True) + "\n")       # the counts-only line is exactly what it was
        self.assertEqual(err, "PUBLISH FAILED: PUBLISH_RECORDS_FAILED\n")                   # a fixed code: no job, title or Notion text

    def test_publish_with_no_failed_record_exits_0(self):
        counts = {"picked": 3, "created": 3, "duplicate": 0, "failed": 0, "seeded": 0, "calls": 9, "fit_sync": {"due": 0, "failed": 0, "synced": 0},
                  "readback": {"checked": 3, "failed": 0, "verified": 3}, "target": "ok"}
        code, out, err = self._main("publish", counts)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "publish: " + json.dumps(counts, sort_keys=True) + "\n")

    def test_publish_that_returned_early_without_a_failed_count_exits_0(self):
        """The wrong-target return carries no `failed` key: it is not this correction's concern and keeps its exit code."""
        code, out, err = self._main("publish", {"calls": 0, "created": 0, "duplicate": 0, "failed": 0, "picked": 0, "seeded": 0, "target": "wrong"})
        self.assertEqual((code, err), (0, ""))
        code, _, err = self._main("publish", {"target": "wrong"})
        self.assertEqual((code, err), (0, ""))

    def test_only_nested_failed_counts_do_not_fail_publish(self):
        """fit_sync and readback keep their own counts; the top-level `failed` (page creation) is what this exit code reports."""
        code, _, err = self._main("publish", {"failed": 0, "fit_sync": {"failed": 2}, "readback": {"failed": 1}})
        self.assertEqual((code, err), (0, ""))

    def test_every_other_stage_keeps_exit_0_even_when_its_counts_say_failed(self):
        others = sorted(name for name in run.STAGES if name not in ("capacity-card", "publish", "fit", "fit-cloud-backlog", "interview", "granola-auto", "job-page-outreach", "mail-filing", "manual-job", "jobs-backlog", "notion-sandbox-access", "notion-sandbox-outreach-proof", "scale-up-exclusion-review", "scale-up-register"))
        self.assertGreater(len(others), 40)
        for name in others:
            code, out, err = self._main(name, {"failed": 3, "lane": "x"})
            self.assertEqual((name, code, err), (name, 0, ""))
            self.assertEqual(out, f"{name}: " + json.dumps({"failed": 3, "lane": "x"}, sort_keys=True) + "\n")

    def test_only_publish_and_fit_are_registered_for_the_partial_failure_exit(self):
        self.assertEqual(sorted(run.PARTIAL_FAILURE), ["capacity-card", "fit", "fit-cloud-backlog", "granola-auto", "interview", "job-page-outreach", "jobs-backlog", "mail-filing", "manual-job", "notion-sandbox-access", "notion-sandbox-outreach-proof", "publish", "scale-up-exclusion-review", "scale-up-register"])     # D219: a degraded backlog batch is a failed step too; LIFE-10: a job page that could not sync, and a refused sandbox read
        self.assertIn("publish", run.STAGES)
        self.assertIn("fit", run.STAGES)

    def test_fit_fails_only_when_its_enabled_cloud_shadow_degraded(self):
        """D214: an off-mode Fit run (failed 0) exits 0; a shadow failure (failed 1, cloud_fit DEGRADED) exits 1 with FIT_CLOUD_DEGRADED, its counts still printed."""
        code, _, err = self._main("fit", {"cloud_fit": "off", "cloud_degraded": 0, "failed": 0, "picked": 3})
        self.assertEqual((code, err), (0, ""))
        code, out, err = self._main("fit", {"cloud_fit": "DEGRADED(FIT_EDGE_REFUSED_DAILY_LIMIT)", "cloud_degraded": 1, "failed": 1, "picked": 3})
        self.assertEqual((code, err), (1, "FIT FAILED: FIT_CLOUD_DEGRADED\n"))
        self.assertIn("DEGRADED(FIT_EDGE_REFUSED_DAILY_LIMIT)", out)

    def test_a_raised_store_or_notion_error_still_returns_1_with_its_own_code(self):
        err = io.StringIO()
        with patch.dict(run.STAGES, {"publish": lambda limit, live: (_ for _ in ()).throw(run.NotionError("NOTION_HTTP_429"))}), contextlib.redirect_stderr(err):
            self.assertEqual(run.main(["publish"]), 1)
        self.assertEqual(err.getvalue(), "PUBLISH FAILED: NOTION_HTTP_429\n")


class PublishThroughTheRealStageTests(unittest.TestCase):
    """D182 end to end: the real publish.run with a fake store and fake Notion client. A failed page creation exits 1; every record write is unchanged."""

    class Cursor:
        def __init__(self, rows, writes):
            self.rows, self.writes, self.result = rows, writes, []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=()):
            if sql.startswith(("UPDATE", "INSERT")):
                self.writes.append((" ".join(sql.split())[:60], tuple(params)[:3]))
                self.result = []
            elif sql.startswith("SELECT COUNT(*)"):
                self.result = [(1,)]                                   # the ledger seed marker exists: no cold-start read
            elif sql.startswith("SELECT j.id, f.admission, f.score"):           # the ordering keys publish reads first (D186): id, admission, score, posted, first surfaced
                self.result = [(r[0], r[17], r[15], r[9], r[8]) for r in self.rows]
            elif "FROM v7_jobs j JOIN v7_job_descriptions" in sql:
                self.result = [r for r in self.rows if r[0] in params]
            else:
                self.result = []                                       # not in the ledger, no same-opening page

        def fetchall(self):
            return self.result

        def fetchone(self):
            return self.result[0] if self.result else None

    class Connection:
        def __init__(self, rows, writes):
            self.rows, self.writes = rows, writes

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return PublishThroughTheRealStageTests.Cursor(self.rows, self.writes)

    @staticmethod
    def _row(job_id, key):
        from datetime import datetime
        return (job_id, key, f"Senior Staff Engineer {job_id}", "Example Co", f"https://example.com/jobs/{job_id}", "web", "greenhouse", "US Remote",
                datetime(2026, 10, 12, 8, 0), None, "A short summary.", None, None, None, None, 70, "fits", None, None, None, None, "direct", None, "", None, None)

    def _publish(self, fail_ids):
        from lifeos.jobs import fit_sync, publish, readback
        writes, created = [], []

        class Client:
            calls = 0
            source = "ledger"

            def __init__(self, environ=None):
                pass

            def call(self, method, path, body=None):
                return {"results": []}

            def create(self, props, blocks):
                job = props["Stable Job Key"]["rich_text"][0]["text"]["content"]
                if job in fail_ids:
                    refused = run.NotionError("NOTION_HTTP_400")
                    refused.detail = "validation_error body.children[2]"
                    raise refused
                created.append(job)
                return "page-" + job

            def create_page(self, props, blocks):
                return {"id": self.create(props, blocks)}

        rows = [self._row(1, "key-1"), self._row(2, "key-2")]
        out, err = io.StringIO(), io.StringIO()
        with patch.object(publish, "Client", Client), patch.object(publish.ledger, "verify", return_value=publish.ledger.OK), \
                patch.object(publish.store, "connect", lambda: self.Connection(rows, writes)), patch.object(publish.store, "ensure_schema", lambda c: None), \
                patch.object(fit_sync, "run", return_value={"due": 0, "failed": 0, "synced": 0}), \
                patch.object(readback, "run", return_value={"checked": 1, "failed": 0, "verified": 1}), patch.object(publish.body, "fill", return_value=0), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run.main(["publish", "--live"])
        return code, out.getvalue(), err.getvalue(), writes, created

    def test_one_failed_page_creation_exits_1_and_every_other_write_is_unchanged(self):
        code, out, err, writes, created = self._publish({"key-1"})
        self.assertEqual(code, 1)
        counts = json.loads(out.split(":", 1)[1])
        self.assertEqual((counts["picked"], counts["created"], counts["failed"]), (2, 1, 1))
        self.assertEqual(counts["error"], "NOTION_HTTP_400 validation_error body.children[2]")
        self.assertEqual(counts["readback"], {"checked": 1, "failed": 0, "verified": 1})
        self.assertEqual(err, "PUBLISH FAILED: PUBLISH_RECORDS_FAILED\n")
        self.assertEqual(created, ["key-2"])                           # the good record is still created ...
        self.assertEqual([w[0][:22] for w in writes], ["UPDATE v7_jobs SET sta", "INSERT IGNORE INTO v7_"])      # ... and recorded at once, exactly as before
        self.assertEqual(writes[0][1][0], "page-key-2")                # the stored page id is the one Notion returned
        self.assertNotIn("Example Co", err + out)                      # no job text in the report

    def test_a_run_with_no_failed_page_exits_0_and_writes_both_records(self):
        code, out, err, writes, created = self._publish(set())
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out.split(":", 1)[1])["created"], 2)
        self.assertEqual(created, ["key-1", "key-2"])
        self.assertEqual(len(writes), 4)


if __name__ == "__main__":
    unittest.main()
