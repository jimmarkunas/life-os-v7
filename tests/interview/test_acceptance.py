import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch
from lifeos.interview import acceptance, stage
from lifeos.interview.models import ParentQuery, RoundQuery
from lifeos.run import STAGES
from tests.interview.test_notion import Fake, ENV

ROOT = Path(__file__).resolve().parents[2]
CASE = {"company": "Example", "role": "Program Manager", "confirmed_round": True,
        "interview_date": "2026-10-01", "interviewer": None, "ordinal": 1}


def env(case=CASE, **extra):
    return {**ENV, "INTERVIEW_ACCEPTANCE_JSON": json.dumps(case), **extra}


def refused(test, environ, code):
    with patch.object(acceptance.stage, "run", side_effect=AssertionError("must not delegate")):
        result = acceptance.run(1, True, environ=environ)
    test.assertEqual((result["writes"], result["writes_planned"], result["blocked"]), (0, 0, 1))
    test.assertEqual(result["why"], {code: 1})
    test.assertNotIn("Example", json.dumps(result))


class SecretContract(unittest.TestCase):
    def test_missing_secret_is_zero_writes(self):
        refused(self, dict(ENV), "acceptance_secret_missing")
        refused(self, {**ENV, "INTERVIEW_ACCEPTANCE_JSON": "  "}, "acceptance_secret_missing")

    def test_malformed_json_and_wrong_shapes(self):
        for raw in ("{", "[]", "null", '"x"', json.dumps(CASE)[:-1], '{"company":"A","company":"B"}'):
            refused(self, {**ENV, "INTERVIEW_ACCEPTANCE_JSON": raw}, "acceptance_secret_invalid")

    def test_extra_and_missing_keys_rejected(self):
        refused(self, env({**CASE, "note": "x"}), "acceptance_secret_invalid")
        refused(self, env({k: v for k, v in CASE.items() if k != "ordinal"}), "acceptance_secret_invalid")

    def test_confirmed_round_must_be_true(self):
        for value in (False, "true", 1, None):
            refused(self, env({**CASE, "confirmed_round": value}), "acceptance_secret_invalid")

    def test_dates_must_be_exact_iso(self):
        for value in ("2026-13-01", "2026-9-29", "09/29/2026", "", None, 20260929):
            refused(self, env({**CASE, "interview_date": value}), "acceptance_secret_invalid")

    def test_round_identity_needs_interviewer_or_ordinal(self):
        refused(self, env({**CASE, "interviewer": None, "ordinal": None}), "acceptance_secret_invalid")
        for who, ordinal in (("  ", None), (None, 0), (None, -1), (None, True), (None, "1"), (7, 1)):
            refused(self, env({**CASE, "interviewer": who, "ordinal": ordinal}), "acceptance_secret_invalid")

    def test_blank_company_or_role_rejected(self):
        for key in ("company", "role"):
            for value in ("", "   ", None, 5):
                refused(self, env({**CASE, key: value}), "acceptance_secret_invalid")

    def test_valid_secret_builds_the_exact_queries(self):
        self.assertEqual(acceptance.parse(json.dumps(CASE)),
                         (ParentQuery("Example", "Program Manager"), RoundQuery(True, "2026-10-01", None, 1)))
        both = {**CASE, "interviewer": " Sam Synthetic ", "ordinal": None}
        self.assertEqual(acceptance.parse(json.dumps(both))[1], RoundQuery(True, "2026-10-01", "Sam Synthetic", None))

    def test_schedule_events_are_refused(self):
        refused(self, env(GITHUB_EVENT_NAME="schedule"), "acceptance_not_manual")


class Delegation(unittest.TestCase):
    def delegate(self, live):
        with patch.object(acceptance.stage, "run", return_value={"writes": 0}) as run:
            acceptance.run(1, live, environ=env())
        return run

    def test_dry_harness_delegates_live_false_with_one_pair(self):
        call = self.delegate(False)
        self.assertEqual(call.call_args.args, (1, False))
        self.assertEqual(len(call.call_args.kwargs["evidence"]), 1)

    def test_live_flag_delegates_live_true(self):
        self.assertEqual(self.delegate(True).call_args.args, (1, True))

    def test_output_carries_no_evidence_and_dry_run_only_reads(self):
        client = Fake()
        result = acceptance.run(1, False, environ=env(), client=client)
        self.assertEqual((result["writes"], result["writes_planned"]), (0, 1))
        self.assertTrue(all(method == "GET" for method, path in client.calls))
        text = json.dumps(result)
        for private in ("Example", "Program Manager", "2026-10-01"):
            self.assertNotIn(private, text)

    def test_stage_is_registered_and_normal_stages_stay_evidence_free(self):
        self.assertIn("interview-acceptance", STAGES)
        for name, target in (("interview", "run"), ("interview-probe", "probe")):
            with patch.object(stage, target, return_value={}) as call:
                STAGES[name](20, False)
            self.assertEqual(call.call_args.args, (20, False))
            self.assertEqual(call.call_args.kwargs, {})

    def test_normal_stage_ignores_the_acceptance_secret(self):
        client = Fake()
        result = stage.run(20, True, environ=env(), client=client)
        self.assertEqual((result["writes"], result["writes_planned"]), (0, 0))
        self.assertTrue(all(method == "GET" for method, path in client.calls))


class WorkflowWiring(unittest.TestCase):
    text = (ROOT / ".github/workflows/hourly.yml").read_text()

    def job(self):
        start = self.text.index("\n  interview:\n")
        return self.text[start:self.text.index("\n  jira:\n")]

    def step(self):
        job = self.job()
        start = job.index("      - id: iacc")
        return job[start:job.index("      - name: Interview warning")]

    def test_step_is_manual_dispatch_with_its_own_input_only(self):
        step = self.step()
        self.assertIn("github.event_name == 'workflow_dispatch'", step)
        self.assertIn("inputs.interview_acceptance == true", step)
        self.assertIn("inputs.tick != true", step)
        self.assertNotIn("schedule", step)
        self.assertRegex(self.text, r"interview_acceptance:\n(?:.*\n)*?\s+default: false")

    def test_secret_is_step_scoped_and_used_once(self):
        self.assertEqual(self.text.count("secrets.INTERVIEW_ACCEPTANCE_JSON"), 1)
        self.assertIn("INTERVIEW_ACCEPTANCE_JSON: ${{ secrets.INTERVIEW_ACCEPTANCE_JSON }}", self.step())
        self.assertNotIn("INTERVIEW_ACCEPTANCE_JSON:", self.text[:self.text.index("\njobs:")])   # never workflow-level env

    def test_job_gate_cannot_run_for_schedule_and_jobs_credentials_stay_blank(self):
        job = self.job()
        self.assertIn("github.event_name == 'workflow_dispatch'", job.split("runs-on")[0])
        for name in ("NOTION_API_TOKEN", "GMAIL_OAUTH_REFRESH_TOKEN", "TINYFISH_API_KEY", "LIFEOS_ACQ_SSH_PRIVATE_KEY", "FIT_PROFILE_JSON"):
            self.assertIn(f'{name}: ""', job)
        self.assertEqual(len(re.findall(r"secrets\.\w+", job)), 2)   # the Interview token and the acceptance case

    def test_command_never_embeds_the_case(self):
        step = self.step()
        self.assertIn("python -m lifeos.run interview-acceptance --limit 1", step)
        self.assertNotRegex(step, r"\"company\"|interview_date")
