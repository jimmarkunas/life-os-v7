"""LIFE-863: the Doppler trial compares by hash, prints no value, and a Doppler problem is a fixed code with exit 0 (the job keeps running on Bitwarden)."""
import json
import re
import unittest
from pathlib import Path

from lifeos.platform import doppler_compare as dc

ROOT = Path(__file__).resolve().parents[2]
NAMES = "TAILSCALE_OAUTH_CLIENT_ID,TAILSCALE_OAUTH_CLIENT_SECRET,IMESSAGE_ALERTS_HOST,NOTION_JIRA_TOKEN"
BW = {"TAILSCALE_OAUTH_CLIENT_ID": "tsid-VALUE-1", "TAILSCALE_OAUTH_CLIENT_SECRET": "tssecret-VALUE-2", "IMESSAGE_ALERTS_HOST": "host-VALUE-3", "NOTION_JIRA_TOKEN": "ntn-VALUE-4"}


def run(doppler, bitwarden=BW, names=NAMES, status="ok", token="dp.st.x", extra=None):
    lines, files = [], {}
    env = {"DOPPLER_TOKEN": token, "DOPPLER_PROJECT": "life-os", "DOPPLER_CONFIG": "prd", "COMPARE_NAMES": names, "BITWARDEN": json.dumps(bitwarden) if not isinstance(bitwarden, str) else bitwarden, **(extra or {})}
    ticks = iter([10.0, 10.25])
    code = dc.main(env, fetcher=lambda *a: (status, doppler if status == "ok" else None), clock=lambda: next(ticks), out=lines.append)
    result = json.loads(next(x for x in lines if x.startswith("doppler-compare: "))[len("doppler-compare: "):])
    return code, result, lines


class Compare(unittest.TestCase):
    def test_a_full_match_by_hash(self):
        code, result, lines = run(dict(BW))
        self.assertEqual((code, result["code"], result["matched"], result["total"], result["doppler_ms"]), (0, "match", 4, 4, 250))
        self.assertTrue(all(x.startswith(("::add-mask::", "doppler-compare: ")) for x in lines))          # masks and one counts line, nothing else

    def test_a_multi_line_doppler_value_is_masked_one_line_per_command_and_never_printed_as_text(self):
        value = "{\n  \"type\": \"service_account\",\n  \"private_key\": \"LINE-TWO-SECRET\"\n}"
        lines = []
        dc.main({"COMPARE_NAMES": "K", "DOPPLER_TOKEN": "t", "DOPPLER_PROJECT": "p", "DOPPLER_CONFIG": "c", "BITWARDEN": "{}"},
                fetcher=lambda *a: ("ok", {"K": value}), clock=lambda: 0.0, out=lines.append)
        self.assertTrue(all(x.startswith(("::add-mask::", "doppler-compare: ")) for x in lines))
        self.assertTrue(all("\n" not in x for x in lines))
        self.assertEqual([x for x in lines if x.startswith("::add-mask::")], ["::add-mask::" + l for l in value.splitlines() if l.strip()])

    def test_a_mismatch_names_the_secret_never_the_value(self):
        code, result, lines = run({**BW, "NOTION_JIRA_TOKEN": "different-VALUE"})
        self.assertEqual((code, result["code"], result["matched"], result["mismatched"]), (0, "mismatch", 3, ["NOTION_JIRA_TOKEN"]))
        text = "\n".join(x for x in lines if not x.startswith("::add-mask::"))
        for value in list(BW.values()) + ["different-VALUE"]:
            self.assertNotIn(value, text)                                                           # no value, from either side, is printed

    def test_a_name_missing_on_either_side_is_its_own_code(self):
        _, result, _ = run({k: v for k, v in BW.items() if k != "IMESSAGE_ALERTS_HOST"})
        self.assertEqual((result["code"], result["missing_in_doppler"]), ("missing_in_doppler", ["IMESSAGE_ALERTS_HOST"]))
        _, result, _ = run(dict(BW), bitwarden={k: v for k, v in BW.items() if k != "NOTION_JIRA_TOKEN"})
        self.assertEqual((result["code"], result["missing_in_bitwarden"]), ("missing_in_bitwarden", ["NOTION_JIRA_TOKEN"]))
        _, result, _ = run({**BW, "NOTION_JIRA_TOKEN": ""})
        self.assertEqual(result["code"], "missing_in_doppler")                                      # an empty value is not a match

    def test_a_doppler_problem_is_a_fixed_code_and_exit_zero(self):
        for status in ("doppler_unreachable", "doppler_unauthorized", "doppler_bad_reply"):
            code, result, lines = run(None, status=status)
            self.assertEqual((code, result["code"], result["matched"]), (0, status, 0), status)
            self.assertFalse([x for x in lines if x.startswith("::add-mask::")])

    def test_bad_input_never_calls_doppler_and_still_exits_zero(self):
        for kwargs in ({"names": ""}, {"token": ""}, {"bitwarden": "not json"}, {"bitwarden": "[1]"}):
            code, result, _ = run(dict(BW), **kwargs)
            self.assertEqual((code, result["code"], result["doppler_ms"]), (0, "bad_input", None), kwargs)

    def test_step_outputs_and_summary_carry_counts_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out, summary = Path(tmp, "out"), Path(tmp, "sum")
            run(dict(BW), extra={"GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)})
            self.assertEqual(out.read_text(), "code=match\nmatched=4\ntotal=4\ndoppler_ms=250\n")
            self.assertNotRegex(summary.read_text(), r"VALUE")


class Workflow(unittest.TestCase):
    """LIFE-981: the compare runs after every Bitwarden load; `DOPPLER_TOKEN` is used only as the token input of the compare step, once per compare step; the job keeps using Bitwarden's values."""
    def test_doppler_token_is_used_only_as_the_token_of_a_compare_step(self):
        for path in (ROOT / ".github").rglob("*.yml"):
            if path == ROOT / ".github/actions/bws-load/action.yml":
                continue                                                                              # LIFE-1010: the loader runs the compare with the token its caller passes in
            from tests import wftext
            text = wftext.text(path)
            self.assertEqual(len(re.findall(r"secrets\.DOPPLER_TOKEN", text)), text.count("uses: ./.github/actions/doppler-load") + len(re.findall(r"^\s+doppler_compare: \$\{\{ secrets\.DOPPLER_TOKEN \}\}$", text, re.M)), str(path))   # LIFE-1010: a loader's doppler_compare input is a compare too

    def test_the_trial_step_cannot_fail_the_job_and_nothing_uses_doppler_values(self):
        import yaml
        doc = yaml.safe_load((ROOT / ".github/workflows/capacity-card.yml").read_text())
        steps = doc["jobs"]["capacitycard"]["steps"]
        trial = next(s for s in steps if s.get("id") == "doppler_trial")
        self.assertTrue(trial["continue-on-error"])
        self.assertEqual(trial["uses"], "./.github/actions/doppler-load")
        self.assertEqual(trial["with"]["token"], "${{ secrets.DOPPLER_TOKEN }}")
        self.assertNotIn("doppler_trial", json.dumps([s for s in steps if s is not trial]))           # no other step reads the trial's output: Bitwarden's values are the ones used
        self.assertEqual(doc["permissions"], {"contents": "read"})
        action = (ROOT / ".github/actions/doppler-load/action.yml").read_text()
        self.assertNotRegex(action, r"uses:")                                                         # no third-party action: stdlib Python from this repository


if __name__ == "__main__":
    unittest.main()
