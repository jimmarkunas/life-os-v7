"""one bad item never fails a stage."""
import contextlib
import io
import unittest
from unittest import mock

from lifeos import run
from lifeos.platform import isolate, settings


class Coded(Exception):
    """A platform-style error: its message is exactly a fixed code."""


def main_exit(stage, name="demo", partial="DEMO_FAILED"):
    """> (exit code, stderr, stdout) of `python -m lifeos.run <name> --live` for a stage function registered as a
    partial-failure stage.
    """
    err, out = io.StringIO(), io.StringIO()
    with mock.patch.dict(run.STAGES, {name: stage}), mock.patch.dict(run.PARTIAL_FAILURE, {name: partial}), mock.patch.object(settings, "apply", return_value={"status": "off"}), \
            mock.patch("lifeos.jobs.daily.record"):
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = run.main([name, "--live"])
    return code, err.getvalue(), out.getvalue()


def five_items(bad):
    """A stage over 5 items where the items in `bad` raise"""
    written = []

    def work(item):
        if item in bad:
            raise RuntimeError(f"secret text for item {item}")
        written.append(item)
        return item * 10

    def stage(limit, live):
        done = isolate.run_items(range(5), work, code_of=lambda error: "DEMO_ITEM_FAILED")
        counts = {"failed": len(done.failures), "written": written}
        return isolate.record(counts, done)
    return stage, written


class Helper(unittest.TestCase):
    def test_five_items_with_one_raising_keep_the_four_good_ones_and_record_one_fixed_code(self):
        done = isolate.run_items(range(5), lambda i: 1 / (i - 2))
        self.assertEqual((done.total, [item for item, _ in done.results], done.failures), (5, [0, 1, 3, 4], [(2, "ITEM_FAILED")]))
        counts = isolate.record({"failed": 1}, done)
        self.assertEqual((counts["items"], counts["item_failed"], counts["item_codes"]), (5, 1, {"ITEM_FAILED": 1}))
        self.assertIs(isolate.stage_fails(counts), False)

    def test_an_exceptions_text_never_becomes_the_code(self):
        done = isolate.run_items([1], lambda i: (_ for _ in ()).throw(RuntimeError("token abc123 host db.internal")))
        self.assertEqual(done.failures, [(1, "ITEM_FAILED")])
        self.assertEqual(isolate.fixed_code(Coded("NOTION_HTTP_429")), "NOTION_HTTP_429")  # a platform error's own fixed code is kept
        self.assertEqual(isolate.fixed_code(Coded("not a code, has spaces")), "ITEM_FAILED")
        error = Coded("x")
        error.code = "STORE_SSH_REFUSED"
        self.assertEqual(isolate.fixed_code(error), "STORE_SSH_REFUSED")

    def test_all_five_failing_fails_the_stage_with_one_fixed_code(self):
        done = isolate.run_items(range(5), lambda i: 1 / 0, code_of=lambda error: "DEMO_ITEM_FAILED")
        counts = isolate.record({"failed": 5}, done)
        self.assertEqual((done.all_failed, counts["item_codes"], isolate.stage_fails(counts)), (True, {"DEMO_ITEM_FAILED": 5}, True))

    def test_a_run_where_every_item_succeeds_adds_nothing_to_the_counts(self):
        done = isolate.run_items(range(5), lambda i: i)
        counts = isolate.record({"failed": 0, "filed": 5}, done)
        self.assertEqual(counts, {"failed": 0, "filed": 5})
        self.assertIsNone(isolate.stage_fails(counts))  # no item outcome: the stage keeps its own rule

    def test_an_empty_list_is_not_a_failed_stage(self):
        done = isolate.run_items([], lambda i: i)
        self.assertEqual((done.total, done.all_failed, isolate.record({}, done)), (0, False, {}))

    def test_a_stage_level_failure_fails_the_stage_even_when_items_succeeded(self):
        self.assertIs(isolate.stage_fails({"stage_failed": 1, "items": 5, "item_failed": 1}), True)

    def test_an_interrupt_and_a_named_stop_are_not_swallowed(self):
        with self.assertRaises(KeyboardInterrupt):
            isolate.run_items([1], lambda i: (_ for _ in ()).throw(KeyboardInterrupt()))
        with self.assertRaises(TimeoutError):
            isolate.run_items([1, 2], lambda i: (_ for _ in ()).throw(TimeoutError("budget")), reraise=(TimeoutError,))


class ExitRule(unittest.TestCase):
    def test_five_items_with_one_raising_exit_zero_and_the_four_good_results_are_written(self):
        stage, written = five_items({3})
        code, err, out = main_exit(stage)
        self.assertEqual((code, written, err), (0, [0, 1, 2, 4], ""))
        self.assertIn('"item_codes": {"DEMO_ITEM_FAILED": 1}', out)  # the failure is recorded under its fixed code
        self.assertNotIn("secret", out + err)

    def test_all_five_failing_exit_one_with_the_one_fixed_code(self):
        stage, written = five_items({0, 1, 2, 3, 4})
        code, err, _ = main_exit(stage)
        self.assertEqual((code, written, err.strip()), (1, [], "DEMO FAILED: DEMO_FAILED"))

    def test_a_failure_of_the_stage_itself_exits_one_even_when_some_items_worked(self):
        def stage(limit, live):
            return {"failed": 1, "stage_failed": 1, "items": 5, "item_failed": 1}
        self.assertEqual(main_exit(stage)[0], 1)

    def test_a_stage_that_reports_no_item_outcome_keeps_the_old_rule(self):
        self.assertEqual(main_exit(lambda limit, live: {"failed": 1})[0], 1)
        self.assertEqual(main_exit(lambda limit, live: {"failed": 0})[0], 0)

    def test_every_item_succeeding_exits_zero_with_the_counts_unchanged(self):
        stage, written = five_items(set())
        code, _, out = main_exit(stage)
        self.assertEqual((code, written), (0, [0, 1, 2, 3, 4]))
        self.assertNotIn("item_failed", out)


if __name__ == "__main__":
    unittest.main()
