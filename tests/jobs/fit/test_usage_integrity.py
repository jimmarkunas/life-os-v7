"""LIFE-41 usage integrity (Jim, description requirements 2 and 3): missing, invalid or inconsistent token counts are DEGRADED(USAGE_UNAVAILABLE) in every mode and
stop further calls; Oracle's own USAGE_UNAVAILABLE refusal (its synthetic check or latch) is the same code with no model call; and the fixed code reaches the ONE
existing phone push (report job -> lifeos.platform.alerts -> ntfy, which Oracle forwards to WhatsApp and Telegram). The push is proven with a stand-in python3 that
records the call: nothing is sent."""
import contextlib
import io
import json
import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from lifeos import run as lifeos_run
from lifeos.jobs.fit import cloud
from tests.jobs.fit.test_backlog import Batch
from tests.jobs.fit import test_cloud
from tests.jobs.fit.test_cloud import Base
from tests.jobs.fit.test_edge import RECEIPT, Endpoint, refusal, reply

try:
    import yaml
except ImportError:
    yaml = None

GOOD = RECEIPT["usage"]


class UsageRule(unittest.TestCase):
    def test_only_whole_consistent_counts_are_usable(self):
        self.assertTrue(cloud.usage_ok(GOOD))                                                   # reasoning "unavailable" (the Gateway omitted it on a real call) is allowed
        for bad in ("unavailable", None, {}, {**GOOD, "total_tokens": "unavailable"}, {**GOOD, "input_tokens": -1}, {**GOOD, "output_tokens": 1.5},
                    {**GOOD, "total_tokens": 9}, {**GOOD, "cached_input_tokens": 5000}, {**GOOD, "reasoning_tokens": 70}, {**GOOD, "input_tokens": True}):
            self.assertFalse(cloud.usage_ok(bad), bad)


class CanaryStopsWithoutUsage(Base):
    CANARY, stage_run, visible = test_cloud.CloudFitCanary.CANARY, test_cloud.CloudFitCanary.stage_run, test_cloud.CloudFitCanary.visible     # its helpers, not its tests

    def test_a_canary_answer_without_usable_counts_is_kept_but_the_tick_reports_usage_unavailable(self):
        for usage in ("unavailable", {**GOOD, "total_tokens": 9}):
            self.setUp()
            self.job(1)
            counts = self.stage_run(self.CANARY, Endpoint(lambda d, u=usage: reply(d, receipt={**RECEIPT, "usage": u})))
            self.assertEqual((counts["cloud_fit"], counts["cloud_promoted"], counts["failed"], counts["cloud_usage_unavailable"]), ("DEGRADED(USAGE_UNAVAILABLE)", 1, 1, 1), usage)
            self.assertEqual(self.visible(1)[4], "CANARY")                                       # the answer and its promotion stand; only the measurement failed

    def test_oracles_usage_refusal_is_the_same_code_with_no_model_call_and_the_job_stays_askable(self):
        self.job(1)
        counts = self.stage_run(self.CANARY, Endpoint(lambda d: refusal(d, "USAGE_UNAVAILABLE")))
        self.assertEqual((counts["cloud_fit"], counts["cloud_ok"], counts["failed"]), ("DEGRADED(USAGE_UNAVAILABLE)", 0, 1))
        self.assertIsNone(self.visible(1)[4])                                                   # claim released: no model ran


class BatchStopsWithoutUsage(Batch):
    def test_inconsistent_counts_stop_a_batch_after_keeping_that_answer(self):
        self.titled(1, "Program Manager A")
        self.titled(2, "Program Manager B", score=70)
        endpoint = Endpoint(lambda d: reply(d, receipt={**RECEIPT, "usage": {**GOOD, "total_tokens": 9}}))
        counts = self.batch(5, endpoint)
        self.assertEqual((counts["cloud_fit"], len(endpoint.sent), counts["cloud_promoted"], counts["cloud_tokens_total"]), ("DEGRADED(USAGE_UNAVAILABLE)", 1, 1, 0))

    def test_oracles_usage_refusal_stops_a_batch_before_any_model_call(self):
        self.titled(1, "Program Manager A")
        endpoint = Endpoint(lambda d: refusal(d, "USAGE_UNAVAILABLE"))
        counts = self.batch(5, endpoint)
        self.assertEqual((counts["cloud_fit"], counts["cloud_ok"], self.status(1)), ("DEGRADED(USAGE_UNAVAILABLE)", 0, None))


class TheCodeReachesThePush(unittest.TestCase):
    """The negative control: a degraded run names USAGE_UNAVAILABLE in the one existing push, once; a clean run pushes nothing. Nothing is sent."""

    def run_main(self, counts, output):
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": output}), mock.patch.object(lifeos_run, "STAGES", {**lifeos_run.STAGES, "fit-cloud-backlog": lambda limit, live: counts}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return lifeos_run.main(["fit-cloud-backlog", "--limit", "1", "--live"])

    def test_the_stage_writes_only_a_fixed_code_as_its_step_output(self):
        with tempfile.TemporaryDirectory() as d:
            out = f"{d}/out"
            self.assertEqual(self.run_main({"cloud_fit": "DEGRADED(USAGE_UNAVAILABLE)", "failed": 1}, out), 1)
            self.assertEqual(open(out).read(), "cloud_fit_code=USAGE_UNAVAILABLE\n")
            open(out, "w").close()
            self.assertEqual(self.run_main({"cloud_fit": "OK", "failed": 0}, out), 0)
            self.assertEqual(open(out).read(), "")
            self.assertEqual(self.run_main({"cloud_fit": "DEGRADED(bad; rm -rf /)", "failed": 1}, out), 1)
            self.assertEqual(open(out).read(), "")                                                # anything but a fixed upper-case code is never written

    @unittest.skipIf(yaml is None, "PyYAML unavailable")
    def test_a_usage_failure_is_named_in_exactly_one_push_and_a_clean_run_pushes_nothing(self):
        doc = yaml.safe_load((pathlib.Path(__file__).resolve().parents[3] / ".github" / "workflows" / "hourly.yml").read_text())
        finish = doc["jobs"]["finish"]
        lanes = next(s for s in finish["steps"] if s.get("id") == "lanes")
        report = next(s for s in doc["jobs"]["report"]["steps"] if "Resolver lanes" in s.get("name", ""))
        self.assertEqual(finish["outputs"]["codes"], "${{ steps.lanes.outputs.codes }}")
        self.assertEqual(report["env"]["CODES"], "${{ needs.finish.outputs.codes }}")
        for step in ("tick", "fit", "fit_cloud_backlog"):
            self.assertIn(f"${{{{ steps.{step}.outputs.cloud_fit_code }}}}", lanes["env"]["CODES"])

        def lanes_run(backlog_outcome, code):
            env_codes = re.sub(r"\$\{\{\s*steps\.fit_cloud_backlog\.outputs\.cloud_fit_code\s*\}\}", code, lanes["env"]["CODES"])
            env_codes = re.sub(r"\$\{\{[^}]+\}\}", "", env_codes)
            script = re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", lambda m: backlog_outcome if m.group(1) == "steps.fit_cloud_backlog.outcome" else "success", lanes["run"])
            with tempfile.TemporaryDirectory() as d:
                done = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], env={**os.environ, "GITHUB_OUTPUT": f"{d}/o", "CODES": env_codes}, capture_output=True, text=True)
                return done.returncode, dict(line.split("=", 1) for line in open(f"{d}/o").read().splitlines())

        def report_run(finish_result, stages, codes):
            script = re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", lambda m: finish_result if m.group(1) == "needs.finish.result" else "success", report["run"])
            with tempfile.TemporaryDirectory() as d:
                shim = pathlib.Path(d, "python3")
                shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{d}/push"\n')
                shim.chmod(stat.S_IRWXU)
                done = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], env={**os.environ, "PATH": f"{d}:{os.environ['PATH']}", "STAGES": stages, "CODES": codes, "NTFY_TOPIC": ""},
                                      capture_output=True, text=True)
                pushed = pathlib.Path(d, "push").read_text().splitlines() if pathlib.Path(d, "push").exists() else None
                return done.returncode, pushed

        code, out = lanes_run("failure", "USAGE_UNAVAILABLE")
        self.assertEqual((code, out["failed"], out["codes"]), (1, "fit_cloud_backlog", "USAGE_UNAVAILABLE"))
        rc, pushed = report_run("failure", out["failed"], out["codes"])
        self.assertEqual(rc, 1)
        self.assertEqual(pushed[:4], ["-m", "lifeos.platform.alerts", "push", "--severity"])        # the existing path, one call
        body = pushed[pushed.index("--body") + 1]
        self.assertIn("stages: fit_cloud_backlog; codes: USAGE_UNAVAILABLE", body)
        code, out = lanes_run("success", "")
        self.assertEqual((code, out["failed"], out["codes"]), (0, "", ""))
        self.assertEqual(report_run("success", "", ""), (0, None))                              # a clean run pushes nothing


if __name__ == "__main__":
    unittest.main()
