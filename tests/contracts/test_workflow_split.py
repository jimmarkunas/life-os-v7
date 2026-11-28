"""domains.yml, hourly.yml and diag.yml are callers: every job lives in one reusable workflow of its own, with the same
runner, timeout, permissions and steps, and one domain failing cannot stop another.
"""

import functools
import pathlib
import re
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import regen  # noqa: E402
import wfsplit  # noqa: E402
import wfsrc  # noqa: E402

FLOWS = ROOT / ".github/workflows"
INPUT = re.compile(r"\binputs\.([A-Za-z_][A-Za-z0-9_]*)")
SECRET = re.compile(r"\bsecrets\.([A-Za-z0-9_]+)")


@functools.lru_cache(maxsize=None)
def _disk(prefix_names):
    return {
        p.name: p.read_text() for p in FLOWS.glob("*.yml") if p.name in prefix_names or p.name.startswith(prefix_names)
    }


def disk(prefix_names):
    return dict(_disk(prefix_names))


@functools.lru_cache(maxsize=None)
def load(name):
    return yaml.safe_load(wfsrc.expand_aliases((FLOWS / name).read_text()))


class Split(unittest.TestCase):
    PAIRS = (("domains", "domains.yml", ("domain-", "domains.yml")), ("hourly", "hourly.yml", ("tick-", "hourly.yml")))

    def logical(self, name):
        return yaml.safe_load(wfsplit.logical_text(name))

    def test_every_old_job_is_in_exactly_one_new_place_with_the_same_content(self):
        for name, caller, prefix in self.PAIRS:
            want = self.logical(name)
            got = wfsplit.flatten(disk(prefix), caller)
            self.assertEqual(list(got["jobs"]), list(want["jobs"]), name)
            for job, body in want["jobs"].items():
                self.assertEqual(got["jobs"][job], body, f"{name}: {job}")
            for key in ("name", "run-name", True, "permissions", "concurrency", "env"):
                self.assertEqual(got.get(key), want.get(key), f"{name}: {key}")

    def test_each_cut_job_has_its_own_file_and_no_file_holds_two_jobs(self):
        for name, caller, prefix in self.PAIRS:
            for job in self.logical(name)["jobs"]:
                holders = [
                    n for n in disk(prefix) if "uses" not in ((load(n).get("jobs") or {}).get(job) or {"uses": 1})
                ]
                self.assertEqual(len(holders), 1, (name, job, holders))
            for n in disk(prefix):
                if n.startswith(prefix[0]) and n != "domain-run.yml":
                    self.assertEqual(len(load(n)["jobs"]), 1, n)

    def test_a_called_workflow_declares_every_input_and_secret_it_reads_and_reads_no_needs(self):
        for n in sorted(FLOWS.glob("*.yml")):
            doc = load(n.name)
            call = (doc.get(True) or {}).get("workflow_call")
            if call is None:
                continue
            text = wfsrc.expand_aliases(n.read_text())
            body = text.split("\njobs:\n", 1)[1]
            self.assertEqual(set(INPUT.findall(body)) - set(call.get("inputs") or {}), set(), n.name)
            self.assertEqual(set(SECRET.findall(body)) - set(call.get("secrets") or {}), set(), n.name)
            if (
                n.name != "domain-run.yml"
            ):  # the orchestrator holds the jobs and their needs; every other called file is one job
                self.assertNotRegex(body, r"\$\{\{[^}]*\bneeds\.", n.name)
            self.assertNotIn("inherit", text, n.name)

    def test_a_caller_passes_exactly_the_secrets_the_called_workflow_declares_and_never_inherit(self):
        for n in sorted(FLOWS.glob("*.yml")):
            for job, body in (load(n.name).get("jobs") or {}).items():
                uses = body.get("uses")
                if not uses:
                    continue
                self.assertNotEqual(body.get("secrets"), "inherit", (n.name, job))
                called = load(uses.rsplit("/", 1)[1])
                declared = set(((called.get(True) or {}).get("workflow_call") or {}).get("secrets") or {})
                self.assertEqual(set(body.get("secrets") or {}), declared, (n.name, job))
                for key in body.get("with") or {}:
                    self.assertIn(
                        key,
                        ((called.get(True) or {}).get("workflow_call") or {}).get("inputs") or {},
                        (n.name, job, key),
                    )

    def test_a_called_job_never_holds_more_permissions_than_its_caller_grants(self):
        order = {"none": 0, "read": 1, "write": 2}
        for n in sorted(FLOWS.glob("*.yml")):
            doc = load(n.name)
            for job, body in (doc.get("jobs") or {}).items():
                if not body.get("uses"):
                    continue
                grant = body.get("permissions") or doc.get("permissions") or {}
                called = load(body["uses"].rsplit("/", 1)[1])
                for inner in (called.get("jobs") or {}).values():
                    held = inner.get("permissions") or called.get("permissions") or {}
                    for scope, level in held.items():
                        self.assertLessEqual(order[level], order[grant.get(scope, "none")], (n.name, job, scope))

    def test_one_domain_failing_never_stops_or_fails_another_domain(self):
        domains = self.logical("domains")["jobs"]
        sinks = {"report", "chain"}
        for job, body in domains.items():
            if job in sinks:
                self.assertTrue(body["if"].startswith("${{ always()"), job)
                continue
            if job == "gate":
                continue
            needs = body.get("needs") or []
            needs = [needs] if isinstance(needs, str) else needs
            self.assertIn("gate", needs, job)
            self.assertTrue(
                body["if"].startswith(("${{ !cancelled()", "${{ always()")), job
            )  # runs whatever a sibling did
            self.assertNotRegex(body["if"], r"needs\.(?!gate\b)\w+\.result\s*==\s*'success'", job)
            self.assertIn("needs.gate.result != 'failure'", body["if"], job)
        hourly = self.logical("hourly")["jobs"]
        for job, body in hourly.items():
            if job.startswith("manual_"):
                self.assertTrue(
                    body["if"].startswith("${{ always()"), job
                )  # a failed manual stage group never skips the next one
        self.assertTrue(
            set(domains["report"]["needs"]) >= set(domains) - sinks
        )  # a failed domain is named and pushed to the phone
        self.assertTrue(set(hourly["report"]["needs"]) >= {j for j in hourly if j.startswith("manual_")} | {"finish"})

    def test_no_workflow_defines_a_key_twice(self):
        """GitHub refuses a file with a repeated key ('permissions' is already defined) and starts nothing from it; PyYAML keeps the last one silently."""

        class Strict(yaml.SafeLoader):
            pass

        def mapping(loader, node, deep=False):
            keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
            repeated = {k for k in keys if keys.count(k) > 1}
            if repeated:
                raise yaml.constructor.ConstructorError(None, None, f"repeated key {repeated}", node.start_mark)
            return yaml.SafeLoader.construct_mapping(loader, node, deep)

        Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        for path in sorted(FLOWS.glob("*.yml")):
            yaml.load(wfsrc.expand_aliases(path.read_text()), Loader=Strict)

    def test_the_generated_files_equal_the_parts(self):
        for path, text in regen.split_files().items():
            self.assertEqual(path.read_text(), text, path.name)


if __name__ == "__main__":
    unittest.main()
