"""LIFE-861: no workflow job loads a Bitwarden secret that no code it runs reads (tools/secret_usage.py), except the few justified entries in secret_allowlist.json."""
import json
import pathlib
import sys
import unittest

try:
    import yaml
except ImportError:                                                         # the workflow contract tests skip the same way
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
ALLOW = json.loads((pathlib.Path(__file__).parent / "secret_allowlist.json").read_text())


def doc(secrets, env, run, set_env="false"):
    return {"jobs": {"job": {"steps": [
        {"id": "bws", "uses": "./.github/actions/bws-load", "with": {"secrets": "\n".join(f"00000000-0000-0000-0000-00000000000{i} > {n}" for i, n in enumerate(secrets)), "set_env": set_env}},
        {"name": "go", "env": {k: "${{ steps.bws.outputs.%s }}" % v for k, v in env.items()}, "run": run}]}}}


@unittest.skipIf(yaml is None, "PyYAML unavailable")
class LeastPrivilege(unittest.TestCase):
    def test_no_job_loads_a_secret_that_none_of_its_code_reads_beyond_the_allowlist(self):
        import secret_usage
        excess, _ = secret_usage.analyse(secret_usage.load_docs())
        allowed = {(e["workflow"], e["job"], e["name"]) for e in ALLOW["entries"]}
        self.assertEqual(sorted(set(excess) - allowed), [], "trim the load (and its env line) from the workflow, or justify it in tests/contracts/secret_allowlist.json")
        self.assertEqual(sorted(allowed - set(excess)), [], "an allowlist entry that is no longer excess: delete it")

    def test_the_allowlist_is_at_most_five_justified_entries(self):
        self.assertLessEqual(len(ALLOW["entries"]), 5)
        for entry in ALLOW["entries"]:
            self.assertTrue(entry["workflow"].endswith(".yml") and entry["job"] and entry["name"] and len(entry["reason"]) > 15, entry)


@unittest.skipIf(yaml is None, "PyYAML unavailable")
class Tool(unittest.TestCase):
    """The checker on small synthetic workflows, with the real stage code: resolve-jobright reads JOBRIGHT_EMAIL and never Gmail."""
    def run_tool(self, d):
        import secret_usage
        return secret_usage.analyse({"x.yml": d})[0]

    def test_a_name_the_stage_reads_is_used_and_one_it_does_not_is_excess(self):
        d = doc(["JOBRIGHT_EMAIL", "GMAIL_OAUTH_CLIENT_ID"], {"JOBRIGHT_EMAIL": "JOBRIGHT_EMAIL", "GMAIL_OAUTH_CLIENT_ID": "GMAIL_OAUTH_CLIENT_ID"}, "python -m lifeos.run resolve-jobright")
        self.assertEqual(self.run_tool(d), [("x.yml", "job", "GMAIL_OAUTH_CLIENT_ID")])

    def test_a_loaded_name_no_step_mentions_is_excess(self):
        d = doc(["JOBRIGHT_EMAIL", "ZZ_NO_SUCH_SECRET"], {"JOBRIGHT_EMAIL": "JOBRIGHT_EMAIL"}, "python -m lifeos.run resolve-jobright")
        self.assertEqual(self.run_tool(d), [("x.yml", "job", "ZZ_NO_SUCH_SECRET")])

    def test_a_name_used_by_a_shell_line_or_an_action_is_used(self):
        d = doc(["SOME_TOKEN"], {"SOME_TOKEN": "SOME_TOKEN"}, 'curl -H "x: $SOME_TOKEN" https://example.invalid')
        self.assertEqual(self.run_tool(d), [])

    def test_a_stage_chosen_at_run_time_counts_every_stage(self):
        d = doc(["GMAIL_OAUTH_CLIENT_ID"], {"GMAIL_OAUTH_CLIENT_ID": "GMAIL_OAUTH_CLIENT_ID"}, 'python -m lifeos.run "$STAGE"')
        self.assertEqual(self.run_tool(d), [])

    def test_a_job_environment_load_is_judged_by_every_command_the_job_runs(self):
        used = doc(["JOBRIGHT_EMAIL"], {}, "python -m lifeos.run resolve-jobright", set_env="true")
        self.assertEqual(self.run_tool(used), [])
        unused = doc(["ZZ_NO_SUCH_SECRET"], {}, "python -m lifeos.run resolve-jobright", set_env="true")
        self.assertEqual(self.run_tool(unused), [("x.yml", "job", "ZZ_NO_SUCH_SECRET")])


if __name__ == "__main__":
    unittest.main()
