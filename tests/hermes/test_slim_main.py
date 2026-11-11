"""LIFE-340: slimming the main profile's config. A temp config shaped like the real one (comments, terminal.cwd '.', five MCP servers); nothing real is touched."""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from tests.kit import this_python

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "hermes", "slim_main.sh")
CONFIG = """# Hermes config (comments are kept)
model:
  default: "gpt-6-luna"
  context_length: 64000
  api_key: SECRET-SHOULD-NEVER-PRINT

terminal:
  backend: local
  cwd: '.'
  docker_mount_cwd_to_workspace: false

stt:
  enabled: true

mcp_servers:
  todoist:
    url: https://ai.todoist.net/mcp
    auth: oauth
    enabled: true
    tools:
      exclude:
        - search
        - fetch
  robinhood:
    url: https://agent.robinhood.com/mcp/trading
    auth: oauth
    enabled: true
    tools:
      include:
        - get_accounts
        - get_alerts
        - get_orders
  plaid:
    url: https://api.dashboard.plaid.com/mcp/
    auth: oauth
    enabled: true
    tools:
      exclude:
        - link_token_create
  paypal:
    url: https://mcp.paypal.com/sse
    auth: oauth
    enabled: false
  figma:
    url: https://mcp.figma.com/mcp
    auth: oauth
    enabled: true

group_sessions_per_user: true
"""


def sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class Slim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "hermes")
        os.makedirs(self.home)
        self.conf = os.path.join(self.home, "config.yaml")
        self.write(CONFIG)
        os.chmod(self.conf, 0o640)
        self.backup = os.path.join(self.tmp, "backup")
        self.work = os.path.join(self.tmp, "work")

    def write(self, text):
        with open(self.conf, "w") as handle:
            handle.write(text)

    def run_script(self, *args):
        done = subprocess.run(["bash", SCRIPT, "--home", self.home, "--backup", self.backup, "--work", self.work, *args], capture_output=True, text=True, env=this_python.env())
        return done.returncode, done.stdout + done.stderr

    def test_dry_run_lists_the_servers_with_tool_counts_plans_the_edits_and_writes_nothing(self):
        before = sha(self.conf)
        status, out = self.run_script("--disable", "robinhood,plaid,figma")
        self.assertEqual(status, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertRegex(out, r"robinhood\s+enabled=true")
        self.assertIn("3 listed to include", out)
        self.assertIn("all but 2 excluded", out)
        self.assertRegex(out, r"paypal\s+enabled=false")
        for planned in ("would set enabled: false for robinhood", "would set enabled: false for plaid", "would set enabled: false for figma", "would set terminal.cwd to " + self.work):
            self.assertIn(planned, out)
        self.assertNotIn("for todoist", out)
        self.assertNotIn("SECRET-SHOULD-NEVER-PRINT", out)
        self.assertEqual(sha(self.conf), before)
        self.assertFalse(os.path.exists(self.backup))
        self.assertFalse(os.path.exists(self.work))

    def test_with_no_disable_list_only_the_working_folder_is_planned(self):
        status, out = self.run_script()
        self.assertEqual(status, 0, out)
        self.assertIn("would set terminal.cwd", out)
        self.assertNotIn("would set enabled: false", out)

    def test_apply_makes_only_the_chosen_edits_keeps_comments_and_mode_backs_up_and_reads_back(self):
        status, out = self.run_script("--apply", "--disable", "robinhood,plaid,figma")
        self.assertEqual(status, 0, out)
        text = open(self.conf).read()
        self.assertIn("  cwd: " + self.work + "\n", text)
        self.assertIn("  todoist:\n    url: https://ai.todoist.net/mcp\n    auth: oauth\n    enabled: true", text)
        self.assertIn("  paypal:\n    url: https://mcp.paypal.com/sse\n    auth: oauth\n    enabled: false", text)
        for kept in ("# Hermes config (comments are kept)", "context_length: 64000", "- get_accounts", "group_sessions_per_user: true", "docker_mount_cwd_to_workspace: false"):
            self.assertIn(kept, text)
        self.assertEqual(open(os.path.join(self.backup, "config.yaml")).read(), CONFIG)
        self.assertEqual(os.stat(self.conf).st_mode & 0o777, 0o640)
        self.assertTrue(os.path.isdir(self.work))
        self.assertEqual(os.stat(self.work).st_mode & 0o777, 0o750)
        self.assertIn("read-back OK", out)
        self.assertIn("ROLLBACK: cp -p %s" % os.path.join(self.backup, "config.yaml"), out)
        self.assertIn("Nothing was restarted", out)
        self.assertNotIn("SECRET-SHOULD-NEVER-PRINT", out)
        import yaml
        new, old = yaml.safe_load(text), yaml.safe_load(CONFIG)
        self.assertEqual(new["terminal"]["cwd"], self.work)
        self.assertEqual({k: v["enabled"] for k, v in new["mcp_servers"].items()}, {"todoist": True, "robinhood": False, "plaid": False, "paypal": False, "figma": False})
        new["terminal"]["cwd"] = "."
        for n in ("robinhood", "plaid", "figma"):
            new["mcp_servers"][n]["enabled"] = True
        self.assertEqual(new, old)

    def test_a_second_run_finds_nothing_to_do(self):
        self.assertEqual(self.run_script("--apply", "--disable", "robinhood")[0], 0)
        after = sha(self.conf)
        status, out = self.run_script("--apply", "--disable", "robinhood")
        self.assertEqual(status, 0)
        self.assertIn("Nothing to do", out)
        self.assertIn("already disabled", out)
        self.assertEqual(sha(self.conf), after)

    def test_no_cwd_changes_only_the_servers(self):
        status, out = self.run_script("--apply", "--no-cwd", "--disable", "figma")
        self.assertEqual(status, 0, out)
        text = open(self.conf).read()
        self.assertIn("  cwd: '.'", text)
        self.assertFalse(os.path.exists(self.work))

    def test_unknown_server_bad_names_bad_work_folder_and_a_missing_config_are_refused_without_changes(self):
        before = sha(self.conf)
        status, out = self.run_script("--apply", "--disable", "nosuch")
        self.assertEqual(status, 1)
        self.assertIn("no MCP server named 'nosuch'", out)
        self.assertEqual(sha(self.conf), before)
        self.assertFalse(os.path.exists(self.backup))
        for bad in (["--disable", "a b"], ["--disable", "x;rm"], ["--work", "relative"], ["--work", "/"], ["--work", "/a/../b"], ["--work", "/x y"]):
            self.assertEqual(subprocess.run(["bash", SCRIPT, "--home", self.home, *bad], capture_output=True, text=True).returncode, 1, bad)
        self.assertEqual(subprocess.run(["bash", SCRIPT, "--home", os.path.join(self.tmp, "none")], capture_output=True, text=True).returncode, 1)
        self.assertEqual(self.run_script("--nonsense")[0], 2)
        self.write(CONFIG.replace("  cwd: '.'\n", ""))
        status, out = self.run_script("--apply")
        self.assertEqual(status, 1)
        self.assertIn("cwd", out)

    def test_an_already_absolute_cwd_is_left_alone(self):
        self.write(CONFIG.replace("cwd: '.'", "cwd: /srv/elsewhere"))
        status, out = self.run_script("--apply", "--disable", "figma")
        self.assertEqual(status, 0, out)
        text = open(self.conf).read()
        self.assertIn("cwd: /srv/elsewhere", text)
        self.assertIn("terminal.cwd is already set", out)

    def test_no_restart_command_in_the_code(self):
        code = "\n".join(l for l in open(SCRIPT).read().splitlines() if not l.lstrip().startswith("#") and "echo" not in l)
        self.assertNotIn("systemctl", code)


if __name__ == "__main__":
    unittest.main()
