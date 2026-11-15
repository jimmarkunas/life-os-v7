"""LIFE-692: every workflow with a `cron:` trigger is in docs/platform/scheduled-checks.md and is either checked by the central scheduled check in lifeos.platform.gate or named there as already covered."""
import pathlib
import re
import unittest

from lifeos.platform import alert_registry, gate

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "platform" / "scheduled-checks.md").read_text(encoding="utf-8")


def scheduled_workflows():
    found = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        if re.search(r"^\s*-\s*cron:", path.read_text(encoding="utf-8"), re.M):
            found.append(path.name)
    return found


class ScheduledChecksContract(unittest.TestCase):
    def test_every_scheduled_workflow_is_checked_or_named_as_covered(self):
        missing = [w for w in scheduled_workflows() if w not in gate.SCHEDULED_WORKFLOWS and w not in gate.SCHEDULED_COVERED]
        self.assertFalse(missing, f"add to gate.SCHEDULED_WORKFLOWS or SCHEDULED_COVERED: {missing}")

    def test_every_scheduled_workflow_is_in_the_doc_table(self):
        missing = [w for w in scheduled_workflows() if f"| `{w}` |" not in DOC]
        self.assertFalse(missing, f"add a row to docs/platform/scheduled-checks.md: {missing}")

    def test_the_check_names_only_workflows_that_are_scheduled(self):
        scheduled = set(scheduled_workflows())
        stale = [w for w in list(gate.SCHEDULED_WORKFLOWS) + list(gate.SCHEDULED_COVERED) if w not in scheduled]
        self.assertFalse(stale, f"no cron trigger left: {stale}")

    def test_each_checked_workflow_has_a_page_key_and_a_recovery_key(self):
        for workflow in gate.SCHEDULED_WORKFLOWS:
            name = workflow.removesuffix(".yml")
            self.assertEqual(alert_registry.severity(f"scheduled_red_{name}"), "page", name)
            self.assertEqual(alert_registry.severity(f"scheduled_recovered_{name}"), "info", name)


if __name__ == "__main__":
    unittest.main()
