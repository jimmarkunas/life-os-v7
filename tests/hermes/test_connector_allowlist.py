"""LIFE-320: the connector-allowlist plugin and its deploy script."""
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERMES = os.path.join(ROOT, "hermes")
PLUGIN = os.path.join(HERMES, "plugins", "connector-allowlist")

spec = importlib.util.spec_from_file_location("connector_allowlist_under_test", os.path.join(PLUGIN, "__init__.py"))
ca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ca)

NOTION = frozenset({"notion"})


class Check(unittest.TestCase):
    def blocked(self, tool, args=None, allowed=NOTION):
        out = ca.check(tool, args, allowed)
        self.assertIsNotNone(out, tool)
        self.assertEqual(out["action"], "block")
        self.assertTrue(out["message"].startswith("connector-allowlist:"))
        return out["message"]

    def test_an_allowed_connector_tool_passes_and_others_are_blocked(self):
        self.assertIsNone(ca.check("connectors__notion__NOTION_FETCH_DATA", {}, NOTION))
        self.assertIsNone(ca.check("Connectors__Notion__X", {}, NOTION))
        for slug in ("robinhood", "plaid", "paypal", "figma", "todoist", "notionx", "notio"):
            self.assertIn(slug, self.blocked("connectors__%s__ANYTHING" % slug))

    def test_a_malformed_connector_tool_name_is_blocked(self):
        self.blocked("connectors__")
        self.blocked("connectors____X")
        self.blocked("connectors__ notion__X")

    def test_nothing_is_allowed_by_an_empty_list(self):
        self.blocked("connectors__notion__X", allowed=frozenset())
        self.blocked("manage_connections", {"action": "status", "connectors": ["notion"]}, frozenset())

    def test_other_tools_are_never_touched(self):
        for tool in ("terminal", "read_file", "tool_search", "delegate_task", "mcp__something", "connector", "my_connectors__x"):
            self.assertIsNone(ca.check(tool, {"connectors": ["plaid"]}, NOTION), tool)
        self.assertIsNone(ca.check(None, None, NOTION))
        self.assertIsNone(ca.check(7, None, NOTION))

    def test_manage_connections_is_limited_to_allowed_connectors_in_every_shape(self):
        for action in ("display", "status", "connect", "reconnect", "rename"):
            self.assertIsNone(ca.check("manage_connections", {"action": action, "connectors": ["notion"]}, NOTION), action)
            self.blocked("manage_connections", {"action": action, "connectors": ["plaid"]})
        self.assertIsNone(ca.check("manage_connections", {"connectors": "Notion"}, NOTION))
        self.assertIsNone(ca.check("manage_connections", {"connectors": {"name": "notion", "alias": "work"}}, NOTION))
        self.assertIsNone(ca.check("manage_connections", {"connectors": [{"name": "notion"}, "notion"]}, NOTION))
        self.blocked("manage_connections", {"connectors": ["notion", "robinhood"]})
        self.blocked("manage_connections", {"connectors": [{"name": "plaid", "alias": "a"}]})
        self.blocked("manage_connections", {"connectors": [" PLAID "]})

    def test_mcp_targets_are_always_blocked_even_with_an_allowed_name(self):
        self.blocked("manage_connections", {"action": "install", "connectors": [{"name": "notion", "mcp": True}]})
        self.blocked("manage_connections", {"action": "authorize", "connectors": [{"name": "anything", "mcp": True}]})

    def test_an_unreadable_manage_connections_call_is_blocked(self):
        for args in (None, {}, {"connectors": []}, {"connectors": None}, {"connectors": 5}, {"connectors": [5]},
                     {"connectors": [{}]}, {"connectors": [{"name": ""}]}, {"connectors": [None]}, "notion", []):
            self.blocked("manage_connections", args)

    def test_the_message_names_the_allowed_list_and_suggests_no_workaround(self):
        message = self.blocked("connectors__plaid__X")
        self.assertIn("notion", message)
        self.assertNotIn("token", message.lower())
        self.assertIn("none", self.blocked("connectors__plaid__X", allowed=frozenset()))


class Loading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, text):
        path = os.path.join(self.tmp, "allowlist.txt")
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def test_names_comments_blank_lines_and_case(self):
        path = self.write("# only these\nnotion\n\n  Todoist  # tasks\n")
        self.assertEqual(ca.load_allowed(path), frozenset({"notion", "todoist"}))

    def test_bad_lines_are_ignored_not_trusted(self):
        path = self.write("notion\n*\nall\n../x\nplaid robinhood\n$(x)\n\n")
        self.assertEqual(ca.load_allowed(path), frozenset({"notion", "all"}))      # 'all' is just a name here, never a wildcard
        self.blocked_wildcard()

    def blocked_wildcard(self):
        self.assertIsNotNone(ca.check("connectors__plaid__X", {}, frozenset({"all"})))
        self.assertIsNotNone(ca.check("connectors__plaid__X", {}, ca.load_allowed(self.write("*\n"))))

    def test_missing_unreadable_and_empty_files_allow_nothing(self):
        self.assertEqual(ca.load_allowed(os.path.join(self.tmp, "nope.txt")), frozenset())
        self.assertEqual(ca.load_allowed(self.write("")), frozenset())
        path = os.path.join(self.tmp, "bin.txt")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\x00\x80")
        self.assertEqual(ca.load_allowed(path), frozenset())
        self.assertEqual(ca.load_allowed(self.tmp), frozenset())                      # a folder, not a file


class Hook(unittest.TestCase):
    def test_the_hook_reads_the_file_on_every_call_and_fails_closed(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "allowlist.txt")
        old = ca.ALLOWLIST
        self.addCleanup(setattr, ca, "ALLOWLIST", old)
        ca.load_allowed.__defaults__ = (path,)
        self.addCleanup(setattr, ca.load_allowed, "__defaults__", (old,))
        self.assertIsNotNone(ca._on_pre_tool_call(tool_name="connectors__notion__X", args={}))          # no file: nothing allowed
        with open(path, "w") as handle:
            handle.write("notion\n")
        self.assertIsNone(ca._on_pre_tool_call(tool_name="connectors__notion__X", args={}, task_id="t", session_id="s"))
        self.assertIsNotNone(ca._on_pre_tool_call(tool_name="connectors__plaid__X", args={}))
        self.assertIsNone(ca._on_pre_tool_call(tool_name="terminal", args={"command": "ls"}))

    def test_a_crash_in_the_check_blocks_connector_calls_and_spares_other_tools(self):
        original = ca.check
        self.addCleanup(setattr, ca, "check", original)

        def boom(*_a, **_k):
            raise RuntimeError("x")
        ca.check = boom
        self.assertEqual(ca._on_pre_tool_call(tool_name="connectors__notion__X", args={})["action"], "block")
        self.assertEqual(ca._on_pre_tool_call(tool_name="manage_connections", args={})["action"], "block")
        self.assertIsNone(ca._on_pre_tool_call(tool_name="terminal", args={}))

    def test_register_hooks_exactly_pre_tool_call(self):
        seen = []

        class Ctx:
            def register_hook(self, name, callback, **_):
                seen.append((name, callback))
        ca.register(Ctx())
        self.assertEqual([n for n, _ in seen], ["pre_tool_call"])
        self.assertIs(seen[0][1], ca._on_pre_tool_call)


class Files(unittest.TestCase):
    def test_the_manifest_declares_the_hook_and_no_tools_and_the_repo_has_no_allowlist_or_secret(self):
        with open(os.path.join(PLUGIN, "plugin.yaml")) as handle:
            text = handle.read()
        self.assertIn("name: connector-allowlist", text)
        self.assertIn("pre_tool_call", text)
        self.assertIn("provides_tools: []", text)
        self.assertEqual(sorted(n for n in os.listdir(PLUGIN) if n != "__pycache__"), ["__init__.py", "plugin.yaml"])      # the allowlist is per host profile, never mastered here
        with open(os.path.join(PLUGIN, "__init__.py")) as handle:
            code = handle.read()
        for banned in ("import os, sys", "subprocess", "socket", "urllib", "requests", "eval(", "exec(", "open(path, \"w"):
            self.assertNotIn(banned, code, banned)


class Deploy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pack = os.path.join(self.tmp, "pack")
        shutil.copytree(HERMES, self.pack, ignore=shutil.ignore_patterns("__pycache__"))
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, "profiles", "librarian"))
        os.makedirs(os.path.join(self.home, "profiles", "writer"))
        self.backup = os.path.join(self.tmp, "backup")
        self.dst = os.path.join(self.home, "profiles", "librarian", "plugins", "connector-allowlist")

    def run_script(self, *args, profile="librarian", allow="notion"):
        cmd = ["bash", os.path.join(self.pack, "deploy_plugin.sh"), "--home", self.home]
        if profile:
            cmd += ["--profile", profile]
        if allow:
            cmd += ["--allow", allow]
        done = subprocess.run(cmd + list(args), capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def test_the_script_has_valid_syntax(self):
        self.assertEqual(subprocess.run(["bash", "-n", os.path.join(HERMES, "deploy_plugin.sh")]).returncode, 0)

    def test_the_default_is_a_dry_run_that_writes_nothing(self):
        status, out = self.run_script()
        self.assertEqual(status, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn("3 file(s) would change", out)
        self.assertFalse(os.path.exists(os.path.join(self.home, "profiles", "librarian", "plugins")))
        self.assertFalse(os.path.exists(self.backup))

    def test_apply_installs_only_in_the_named_profile_reads_back_and_a_second_apply_changes_nothing(self):
        status, out = self.run_script("--apply", "--backup", self.backup)
        self.assertEqual(status, 0, out)
        self.assertIn("read-back OK", out)
        for name in ("plugin.yaml", "__init__.py"):
            with open(os.path.join(self.dst, name)) as got, open(os.path.join(PLUGIN, name)) as want:
                self.assertEqual(got.read(), want.read(), name)
        with open(os.path.join(self.dst, "allowlist.txt")) as handle:
            self.assertEqual(handle.read(), "notion\n")
        for name in ("plugin.yaml", "__init__.py", "allowlist.txt"):
            self.assertEqual(os.stat(os.path.join(self.dst, name)).st_mode & 0o777, 0o600, name)
        self.assertEqual(os.stat(self.dst).st_mode & 0o777, 0o700)
        self.assertFalse(os.path.exists(os.path.join(self.home, "profiles", "writer", "plugins")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "plugins")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "config.yaml")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "profiles", "librarian", "config.yaml")))
        self.assertIn("Not enabled and nothing restarted", out)
        status, again = self.run_script("--apply", "--backup", os.path.join(self.tmp, "backup2"))
        self.assertEqual(status, 0, again)
        self.assertIn("0 file(s) would change", again)

    def test_a_changed_allowlist_is_backed_up_and_has_a_rollback_line(self):
        self.assertEqual(self.run_script("--apply", "--backup", self.backup)[0], 0)
        status, out = self.run_script("--apply", "--backup", os.path.join(self.tmp, "b2"), allow="notion,todoist")
        self.assertEqual(status, 0, out)
        self.assertIn("1 file(s) would change", out)
        with open(os.path.join(self.dst, "allowlist.txt")) as handle:
            self.assertEqual(handle.read(), "notion\ntodoist\n")
        with open(os.path.join(self.tmp, "b2", "allowlist.txt")) as handle:
            self.assertEqual(handle.read(), "notion\n")
        self.assertIn("ROLLBACK allowlist.txt", out)

    def test_bad_arguments_are_refused_before_anything_is_written(self):
        for kwargs, needle in (({"profile": ""}, "--profile NAME is required"), ({"profile": "../x"}, "--profile NAME is required"),
                               ({"allow": ""}, "--allow NAMES is required"), ({"allow": "notion,*"}, "is not a connector name"),
                               ({"allow": "notion,$(x)"}, "is not a connector name"), ({"profile": "nosuch"}, "no profile folder")):
            status, out = self.run_script("--apply", "--backup", self.backup, **kwargs)
            self.assertEqual(status, 1, (kwargs, out))
            self.assertIn(needle, out)
        self.assertFalse(os.path.exists(os.path.join(self.home, "profiles", "librarian", "plugins")))
        self.assertFalse(os.path.exists(self.backup))

    def test_a_missing_repo_file_is_refused(self):
        os.remove(os.path.join(self.pack, "plugins", "connector-allowlist", "__init__.py"))
        status, out = self.run_script()
        self.assertEqual(status, 1, out)
        self.assertIn("missing or empty", out)

    def test_output_never_contains_code(self):
        status, out = self.run_script("--apply", "--backup", self.backup)
        self.assertEqual(status, 0, out)
        self.assertNotIn("def check", out)
        self.assertNotIn("register_hook", out)


if __name__ == "__main__":
    unittest.main()
