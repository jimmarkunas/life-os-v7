"""LIFE-779: the standing pre-approval gate (tools/workflow_gate.py). A workflow change with no new secret and no wider permissions needs no approval; each stop reason is proven by a test that fails if the check is removed."""
import importlib.util
import io
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("workflow_gate", ROOT / "tools" / "workflow_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

BASE = """name: demo
on: [workflow_dispatch]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - run: echo hello
        env:
          TOKEN: ${{ secrets.EXISTING }}
  other:
    runs-on: ubuntu-24.04
    steps:
      - id: bws
        uses: ./.github/actions/bws-load
        with:
          secrets: |
            11111111-1111-1111-1111-111111111111 > KNOWN_ITEM
      - run: echo other
"""


def check(head, base=BASE):
    return gate.reasons_for(".github/workflows/demo.yml", base, head)


class PreApproved(unittest.TestCase):
    def test_a_plain_edit_needs_no_approval(self):
        head = BASE.replace("echo hello", "echo hello world").replace("runs-on: ubuntu-24.04\n    steps:\n      - run", "runs-on: ubuntu-24.04\n    timeout-minutes: 5\n    steps:\n      - run", 1)
        self.assertEqual(check(head), [])

    def test_a_new_job_with_no_secret_and_no_permission_needs_no_approval(self):
        head = BASE + "  extra:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo extra\n"
        self.assertEqual(check(head), [])

    def test_narrowing_permissions_needs_no_approval(self):
        head = BASE.replace("  contents: read\n", "  contents: none\n")
        self.assertEqual(check(head), [])

    def test_a_comment_that_names_a_secret_is_not_a_secret(self):
        head = BASE + "# secrets.NEW_THING is only mentioned here\n"
        self.assertEqual(check(head), [])
        head = BASE.replace("echo other", "echo other # secrets.FOO is a comment")
        self.assertEqual(check(head), [])


class Stops(unittest.TestCase):
    def test_a_new_secret_stops(self):
        head = BASE.replace("TOKEN: ${{ secrets.EXISTING }}", "TOKEN: ${{ secrets.EXISTING }}\n          NEW: ${{ secrets.NEW_THING }}")
        reasons = check(head)
        self.assertTrue(any(r.startswith("new secret") and "NEW_THING" in r for r in reasons), reasons)

    def test_an_existing_secret_reaching_another_job_stops(self):
        head = BASE.replace("      - run: echo other", "      - run: echo other\n        env:\n          T: ${{ secrets.EXISTING }}")
        self.assertTrue(any(r.startswith("new secret") and "other" in r for r in check(head)))

    def test_secrets_inherit_stops(self):
        head = BASE + "  caller:\n    uses: ./.github/workflows/child.yml\n    secrets: inherit\n"
        self.assertTrue(any("secrets: inherit" in r for r in check(head)))

    def test_a_widened_workflow_permission_stops(self):
        head = BASE.replace("  contents: read\n", "  contents: write\n")
        self.assertTrue(any(r.startswith("wider permissions") for r in check(head)))

    def test_a_new_permission_scope_stops(self):
        head = BASE.replace("  contents: read\n", "  contents: read\n  id-token: write\n")
        self.assertTrue(any(r.startswith("wider permissions") for r in check(head)))

    def test_a_job_block_wider_than_the_workflow_stops(self):
        head = BASE.replace("  other:\n    runs-on: ubuntu-24.04\n", "  other:\n    runs-on: ubuntu-24.04\n    permissions:\n      contents: write\n")
        self.assertTrue(any(r.startswith("wider permissions") and "other" in r for r in check(head)))

    def test_a_new_job_with_its_own_wider_block_stops(self):
        head = BASE + "  extra:\n    runs-on: ubuntu-24.04\n    permissions:\n      issues: write\n    steps:\n      - run: echo extra\n"
        self.assertTrue(any(r.startswith("wider permissions") and "extra" in r for r in check(head)))

    def test_a_new_bitwarden_load_stops(self):
        head = BASE.replace("KNOWN_ITEM\n", "KNOWN_ITEM\n            22222222-2222-2222-2222-222222222222 > NEW_ITEM\n")
        self.assertTrue(any(r.startswith("new credential") and "NEW_ITEM" in r for r in check(head)))

    def test_deleting_github_values_or_data_stops(self):
        for line in ("gh secret delete OLD_ONE", "gh variable delete OLD_ONE", "mysql -e 'DROP TABLE v7_old'"):
            head = BASE + "  clean:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: %s\n" % line
            self.assertTrue(any(r.startswith("deleting") for r in check(head)), line)

    def test_paid_model_spend_stops(self):
        head = BASE + "  ask:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: claude -p 'summarize'\n"
        self.assertTrue(any(r.startswith("paid-model spend") for r in check(head)))


class Cli(unittest.TestCase):
    def repo(self, head):
        tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(tmp.name)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": tmp.name}
        run = lambda *a: subprocess.run(a, cwd=root, env=env, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        (root / ".github" / "workflows").mkdir(parents=True)
        path = root / ".github" / "workflows" / "demo.yml"
        path.write_text(BASE)
        run("git", "add", ".")
        run("git", "commit", "-q", "-m", "base")
        run("git", "checkout", "-q", "-b", "topic")
        path.write_text(head)
        run("git", "commit", "-qam", "head")
        self.addCleanup(tmp.cleanup)
        return root

    def run_gate(self, head, environ=None):
        root = self.repo(head)
        out = io.StringIO()
        original = gate.ROOT
        gate.ROOT = root
        try:
            reasons = gate.verdict("main", "HEAD", root)
            code = 0 if not reasons else 5
            if reasons and not (environ or {}).get("LIFEOS_JIM_APPROVED"):
                for reason in reasons:
                    out.write("[waiting on] Jim: %s\n" % reason)
            else:
                code = 0
        finally:
            gate.ROOT = original
        return code, out.getvalue(), reasons

    def test_a_pure_edit_passes_with_no_approval_step(self):
        code, text, reasons = self.run_gate(BASE.replace("echo hello", "echo hello again"))
        self.assertEqual((code, reasons, text), (0, [], ""))

    def test_a_new_secret_stops_with_the_waiting_line(self):
        code, text, reasons = self.run_gate(BASE.replace("TOKEN: ${{ secrets.EXISTING }}", "TOKEN: ${{ secrets.EXISTING }}\n          NEW: ${{ secrets.NEW_THING }}"))
        self.assertEqual(code, 5)
        self.assertIn("[waiting on] Jim: new secret", text)

    def test_a_widened_permission_stops_with_the_waiting_line(self):
        code, text, reasons = self.run_gate(BASE.replace("  contents: read\n", "  contents: write\n"))
        self.assertEqual(code, 5)
        self.assertIn("[waiting on] Jim: wider permissions", text)

    def test_main_stops_then_passes_once_jim_said_yes(self):
        root = self.repo(BASE.replace("  contents: read\n", "  contents: write\n"))
        refused, accepted = io.StringIO(), io.StringIO()
        original = gate.ROOT
        gate.ROOT = root
        try:
            self.assertEqual(gate.main(["--base", "main"], refused, {}), 5)
            self.assertEqual(gate.main(["--base", "main"], accepted, {"LIFEOS_JIM_APPROVED": "[approvals] permissions, Jim, 5:40 PM CT"}), 0)
        finally:
            gate.ROOT = original
        self.assertIn("[waiting on] Jim:", refused.getvalue())
        self.assertIn("Jim approved", accepted.getvalue())


if __name__ == "__main__":
    unittest.main()
