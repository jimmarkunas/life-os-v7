import pathlib
import unittest

try:
    import yaml
except ImportError:          # PyYAML is a dev convenience, not a runtime dependency
    yaml = None


@unittest.skipIf(yaml is None, "PyYAML not installed")
class WorkflowTests(unittest.TestCase):
    def test_hourly_workflow_is_valid_yaml_with_dispatch_inputs(self):
        path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "hourly.yml"
        data = yaml.safe_load(path.read_text())
        self.assertIn("workflow_dispatch", data[True])
        self.assertIn("live", data[True]["workflow_dispatch"]["inputs"])

    def test_dice_resolver_is_pipeline_only_and_adds_no_dispatch_input(self):
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "hourly.yml"
        data = yaml.safe_load(path.read_text())
        self.assertNotIn("resolve_dice", data[True]["workflow_dispatch"]["inputs"])
        job = data["jobs"]["dice"]
        step = next(step for step in job["steps"] if step.get("id") == "dice")
        self.assertIn("PIPELINE", step["if"])
        self.assertNotIn("inputs.", step["if"])

    def test_agenda_resolver_adds_no_hourly_dispatch_input(self):
        from pathlib import Path
        data = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/hourly.yml").read_text())
        self.assertNotIn("agenda", data[True]["workflow_dispatch"]["inputs"])
        job = data["jobs"]["agenda"]
        self.assertIn("inputs.tick", job["if"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(yaml is None, "PyYAML not installed")
class IsolatedJobTests(unittest.TestCase):
    """Isolated OS jobs blank every workflow-level secret unless a step redeclares only what it needs.
    must be blanked in each of them or this fails (the isolation pattern is declared three times, so it is checked, not trusted)."""

    def test_no_workflow_level_secret_reaches_an_isolated_job(self):
        import yaml
        from pathlib import Path
        doc = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/hourly.yml").read_text())
        shared = {k: v for k, v in (doc.get("env") or {}).items() if "secrets." in str(v)}
        self.assertGreater(len(shared), 10)
        needs = {"interview": {"HIRING_PIPELINE_PAGE_ID"}}                  # the Interview job reads the Hiring Pipeline page by id
        for name in ("interview", "jira", "outlook", "bills", "agenda"):
            env = doc["jobs"][name].get("env") or {}
            leaked = sorted(k for k in shared if k not in needs.get(name, ()) and env.get(k) not in ("",))
            self.assertEqual(leaked, [], f"{name} inherits {leaked}")

    def test_bills_receives_only_its_notion_and_database_credentials(self):
        import yaml
        from pathlib import Path
        doc = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/hourly.yml").read_text())
        job = doc["jobs"]["bills"]
        step = next(step for step in job["steps"] if step.get("id") == "bsnap")
        self.assertEqual(set(step.get("env", {})), {
            "NOTION_BILLS_TOKEN", "NOTION_BILLS_DATA_SOURCE_ID", "LIFEOS_ACQ_SSH_PRIVATE_KEY",
            "LIFEOS_ACQ_DB_PASSWORD", "LIFEOS_ACQ_SSH_HOST", "LIFEOS_ACQ_SSH_PORT",
            "LIFEOS_ACQ_SSH_USER", "LIFEOS_ACQ_SSH_KNOWN_HOSTS", "LIFEOS_ACQ_DB_NAME", "LIFEOS_ACQ_DB_USER"})
    def test_agenda_steps_receive_only_calendar_card_and_database_secrets(self):
        import yaml
        from pathlib import Path
        doc = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/hourly.yml").read_text())
        job = doc["jobs"]["agenda"]
        steps = {step["id"]: step for step in job["steps"] if step.get("id") in ("asnap", "acard")}
        db = {f"LIFEOS_ACQ_{key}" for key in ("SSH_PRIVATE_KEY", "DB_PASSWORD", "SSH_HOST", "SSH_PORT", "SSH_USER", "SSH_KNOWN_HOSTS", "DB_NAME", "DB_USER")}
        self.assertEqual(set(steps["asnap"]["env"]), db | {"GCAL_SERVICE_ACCOUNT_JSON", "GCAL_CALENDAR_ID"})
        self.assertEqual(set(steps["acard"]["env"]), db | {"NOTION_JIRA_TOKEN", "CALENDAR_CARD_BLOCK_ID"})

    def test_python_setup_is_declared_once(self):
        from pathlib import Path
        for path in (Path(__file__).resolve().parents[2] / ".github/workflows").glob("*.yml"):
            self.assertNotIn("setup-python", path.read_text(), path.name)
