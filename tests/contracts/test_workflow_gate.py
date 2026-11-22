"""LIFE-779: the standing pre-approval gate (tools/workflow_gate.py). A workflow change with no new secret and no wider permissions needs no approval; each stop reason is proven by a test that fails if the check is removed."""
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tempfile
import time
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


# LIFE-908: the approval Jim recorded in a LIFE ticket (ChatGPT, then the ticket's "Jim must do first" section) is read from Jira, never asked for twice; anything it does not name still stops.
DOMAINS_BASE = """name: domains
on: [workflow_dispatch]
permissions:
  contents: read
jobs:
  existing:
    runs-on: ubuntu-24.04
    steps:
      - run: echo hello
"""
CHAIN = """  chain:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      actions: %s
    steps:
      - run: echo chain
"""
APPROVED_ITEM = ("* \\[x\\] **LIFE-904 exact workflow read-permission authorization \u2014 Jim's instruction in ChatGPT, November 22, 2026:** Approve **only** the new `chain` job in "
                 "`.github/workflows/domains.yml` declaring `contents: read` and `actions: read` for the three correlated clocks. No `write` scope, other job permission, additional secret/credential, "
                 "deletion, spend, schedule/cadence change or production data mutation is approved by this item.")
TRANSFER_ITEM = ("* \\[x\\] **Approval transfer to agent:** this Jira description is the canonical owner-authorization record. If the ONLY gate reason is `wider permissions (.github/workflows/domains.yml: chain)`, "
                 "use `[approvals] LIFE-904: Jim approved domains.yml chain job contents: read and actions: read (ChatGPT Nov 22 2026)` via `LIFEOS_JIM_APPROVED`.")


def description(*items):
    return "## Outcome\n\nx\n\n## Jim must do first\n\n" + "\n".join(items) + "\n\n## Not in scope\n\nnothing\n"


def envelope(key="LIFE-904", status="In Progress", body=None):
    fields = {"summary": "s", "status": {"name": status}, "labels": [], "parent": None, "components": [], "issuelinks": [], "description": description(APPROVED_ITEM, TRANSFER_ITEM) if body is None else body,
              "updated": "2026-11-22T15:00:00.000-0600"}
    return {"issues": {"nodes": [{"key": key, "fields": fields}]}}


class JiraApproval(unittest.TestCase):
    BRANCH = "life/LIFE-904/chain-job"

    def run_gate(self, head, issue=None, environ=None, branch=None, read=True, age=0):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": tmp.name}
        git = lambda *a: subprocess.run(a, cwd=root, env=env, capture_output=True, text=True, check=True)
        git("git", "init", "-q", "-b", "main")
        (root / ".github" / "workflows").mkdir(parents=True)
        wf = root / ".github" / "workflows" / "domains.yml"
        wf.write_text(DOMAINS_BASE)
        git("git", "add", ".")
        git("git", "commit", "-q", "-m", "base")
        git("git", "checkout", "-q", "-b", "topic")
        wf.write_text(head)
        git("git", "commit", "-qam", "head")
        args = ["--base", "main"]
        if read:
            saved = root / "jira-read.json"
            saved.write_text(json.dumps(envelope() if issue is None else issue))
            if age:
                os.utime(saved, (time.time() - age, time.time() - age))
            args += ["--jira-read", str(saved)]
        out, original = io.StringIO(), gate.ROOT
        gate.ROOT = root
        try:
            code = gate.main(args, out, environ or {}, branch=self.BRANCH if branch is None else branch)
        finally:
            gate.ROOT = original
        return code, out.getvalue()

    HEAD = DOMAINS_BASE + CHAIN % "read"

    def test_the_recorded_livelife_904_approval_passes_with_no_typed_yes_and_prints_safe_proof(self):
        code, text = self.run_gate(self.HEAD)
        self.assertEqual(code, 0, text)
        self.assertIn("workflow gate: Jira-approved (LIFE-904, mcp-read file", text)
        self.assertIn(".github/workflows/domains.yml: chain: actions:read", text)
        self.assertNotIn("Jim's instruction", text)                                   # the description is never echoed
        self.assertNotIn("[waiting on]", text)

    def test_the_approval_items_of_the_real_ticket_text_are_exactly_one(self):
        items = gate.approved_items(description(APPROVED_ITEM, TRANSFER_ITEM))
        self.assertEqual(items, [{"path": ".github/workflows/domains.yml", "job": "chain", "scopes": {"contents": "read", "actions": "read"}}])

    def test_a_write_scope_stops(self):
        code, text = self.run_gate(DOMAINS_BASE + CHAIN % "write")
        self.assertEqual(code, 5)
        self.assertIn("[waiting on] Jim: wider permissions (.github/workflows/domains.yml: chain)", text)

    def test_a_scope_the_item_does_not_name_stops(self):
        code, text = self.run_gate(DOMAINS_BASE + (CHAIN % "read").replace("      actions: read\n", "      actions: read\n      issues: read\n"))
        self.assertEqual(code, 5)
        self.assertIn("do not cover", text)

    def test_a_new_secret_or_credential_beside_the_approved_scopes_stops(self):
        code, text = self.run_gate(DOMAINS_BASE + (CHAIN % "read").replace("- run: echo chain", "- run: echo chain\n        env:\n          T: ${{ secrets.NEW_THING }}"))
        self.assertEqual(code, 5)
        self.assertIn("[waiting on] Jim: new secret", text)

    def test_spend_or_deletion_beside_the_approved_scopes_stops(self):
        code, text = self.run_gate(DOMAINS_BASE + (CHAIN % "read").replace("echo chain", "curl https://api.anthropic.com/v1"))
        self.assertEqual(code, 5)

    def test_the_wrong_job_stops(self):
        code, text = self.run_gate(DOMAINS_BASE + (CHAIN % "read").replace("  chain:", "  other:"))
        self.assertEqual(code, 5)
        self.assertIn("(.github/workflows/domains.yml: other)", text)

    def test_the_wrong_path_stops(self):
        wrong = description(APPROVED_ITEM.replace("domains.yml", "hourly.yml"))
        code, text = self.run_gate(self.HEAD, issue=envelope(body=wrong))
        self.assertEqual(code, 5)

    def test_an_unchecked_item_stops(self):
        code, text = self.run_gate(self.HEAD, issue=envelope(body=description(APPROVED_ITEM.replace("\\[x\\]", "\\[ \\]"))))
        self.assertEqual(code, 5)

    def test_an_item_that_does_not_name_jim_and_chatgpt_or_names_a_write_stops(self):
        for item in (APPROVED_ITEM.replace("ChatGPT", "chat"), APPROVED_ITEM.replace("Jim's", "the"), APPROVED_ITEM.replace("`actions: read`", "`actions: write`"),
                     APPROVED_ITEM.replace("`chain` job", "`chain` and `existing` job")):
            self.assertEqual(gate.approved_items(description(item)), [], item)

    def test_an_approval_outside_the_jim_must_do_first_section_is_ignored(self):
        body = "## Outcome\n\n" + APPROVED_ITEM + "\n\n## Jim must do first\n\nNone.\n"
        code, text = self.run_gate(self.HEAD, issue=envelope(body=body))
        self.assertEqual(code, 5)

    def test_another_ticket_is_never_inherited(self):
        code, text = self.run_gate(self.HEAD, issue=envelope(key="LIFE-908"))
        self.assertEqual(code, 5)
        self.assertIn("KEY_MISMATCH", text)
        code, text = self.run_gate(self.HEAD, branch="life/LIFE-905/other")
        self.assertEqual(code, 5)

    def test_a_ticket_that_is_not_in_progress_stops(self):
        for status in ("Agent Go", "Done", "To Do"):
            code, text = self.run_gate(self.HEAD, issue=envelope(status=status))
            self.assertEqual(code, 5, status)
            self.assertIn("not In Progress", text)

    def test_a_stale_saved_read_stops(self):
        code, text = self.run_gate(self.HEAD, age=16 * 60)
        self.assertEqual(code, 5)
        self.assertIn("JIRA_READ_STALE", text)

    def test_no_jira_read_at_all_stops_with_the_narrow_handoff(self):
        code, text = self.run_gate(self.HEAD, read=False)
        self.assertEqual(code, 5)
        self.assertIn("BLOCKED", text)
        self.assertIn("LIFEOS_JIRA_READ", text)

    def test_a_malformed_or_unreadable_saved_read_stops(self):
        for issue in ({}, {"issues": {"nodes": []}}, {"issues": {"nodes": [{"key": "LIFE-904", "fields": {}}]}}):
            code, text = self.run_gate(self.HEAD, issue=issue)
            self.assertEqual(code, 5)

    def test_a_branch_that_names_no_ticket_stops(self):
        for branch in ("topic", "HEAD", "life/not-a-key/x"):
            code, text = self.run_gate(self.HEAD, branch=branch)
            self.assertEqual(code, 5, branch)

    def test_the_live_jira_read_is_used_when_the_settings_are_set_and_its_failure_stops(self):
        from unittest import mock
        live = {"JIRA_BASE_URL": "https://example.atlassian.net", "JIRA_EMAIL": "a@example.com", "JIRA_API_TOKEN": "x"}
        raw = envelope()["issues"]["nodes"][0]
        with mock.patch("lifeos.jira.readiness_record.fetch", return_value=raw):
            code, text = self.run_gate(self.HEAD, environ=live, read=False)
        self.assertEqual(code, 0, text)
        self.assertIn("live Jira read", text)
        with mock.patch("lifeos.jira.readiness_record.fetch", side_effect=RuntimeError("JIRA_HTTP_500")):
            code, text = self.run_gate(self.HEAD, environ=live, read=False)
        self.assertEqual(code, 5)
        self.assertIn("BLOCKED", text)

    def test_a_typed_environment_string_is_unchanged_and_the_jira_path_never_needs_it(self):
        code, text = self.run_gate(DOMAINS_BASE + CHAIN % "write", environ={"LIFEOS_JIM_APPROVED": "[approvals] write, Jim, 5:40 PM CT"})
        self.assertEqual(code, 0)
        self.assertIn("Jim approved", text)


if __name__ == "__main__":
    unittest.main()
