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
        for tool in ("terminal", "read_file", "tool_search", "delegate_task", "connector", "my_connectors__x"):
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


class McpTools(unittest.TestCase):
    """LIFE-320 follow-up (live test, Nov 6 2026): Todoist and Robinhood reached the librarian as MCP servers, tools named mcp__<server>__<tool>."""

    def test_every_mcp_tool_is_blocked_unless_its_server_is_listed(self):
        for tool in ("mcp__todoist__find-projects", "mcp__robinhood__place_order", "mcp__robinhood__list_resources", "MCP__Todoist__X", "mcp__", "mcp____x", "mcp__notion__x"):
            out = ca.check(tool, {}, NOTION)
            self.assertEqual(out["action"], "block", tool)
            self.assertIn("MCP server", out["message"], tool)
        self.assertEqual(ca.check("mcp__todoist__x", {}, frozenset())["action"], "block")

    def test_a_listed_mcp_server_passes_and_only_that_one(self):
        allowed = frozenset({"notion", "mcp:todoist"})
        self.assertIsNone(ca.check("mcp__todoist__find-projects", {}, allowed))
        self.assertIsNone(ca.check("mcp__Todoist__find-projects", {}, allowed))
        self.assertEqual(ca.check("mcp__robinhood__x", {}, allowed)["action"], "block")
        self.assertEqual(ca.check("mcp__todoist2__x", {}, allowed)["action"], "block")
        self.assertEqual(ca.check("mcp__todois__x", {}, allowed)["action"], "block")
        self.assertEqual(ca.check("connectors__todoist__x", {}, allowed)["action"], "block")       # a connector is not an MCP server

    def test_a_connector_name_never_allows_an_mcp_server_and_the_reverse(self):
        self.assertEqual(ca.check("mcp__notion__x", {}, frozenset({"notion"}))["action"], "block")
        self.assertEqual(ca.check("connectors__notion__x", {}, frozenset({"mcp:notion"}))["action"], "block")

    def test_the_file_format_accepts_mcp_lines_and_ignores_bad_ones(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "allowlist.txt")
        with open(path, "w") as handle:
            handle.write("notion\nmcp:todoist  # tasks\nmcp:\nmcp:*\nmcp:mcp:x\n")
        self.assertEqual(ca.load_allowed(path), frozenset({"notion", "mcp:todoist"}))

    def test_a_crash_blocks_mcp_tools_too(self):
        original = ca.check
        self.addCleanup(setattr, ca, "check", original)

        def boom(*_a, **_k):
            raise RuntimeError("x")
        ca.check = boom
        self.assertEqual(ca._on_pre_tool_call(tool_name="mcp__robinhood__x", args={})["action"], "block")


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
        self.assertIn("Nothing was enabled or restarted", out)
        self.assertIn("hermes --profile librarian plugins enable connector-allowlist", out)
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
        with open(os.path.join(self.tmp, "b2", "librarian", "allowlist.txt")) as handle:
            self.assertEqual(handle.read(), "notion\n")
        self.assertIn("ROLLBACK librarian/allowlist.txt", out)

    def test_bad_arguments_are_refused_before_anything_is_written(self):
        for kwargs, needle in (({"profile": ""}, "--profile NAME is required"), ({"profile": "../x"}, "--profile NAME is required"),
                               ({"allow": ""}, "--allow NAMES is required"), ({"allow": "notion,*"}, "is not an allowed name"),
                               ({"allow": "notion,$(x)"}, "is not an allowed name"), ({"profile": "nosuch"}, "no profile folder")):
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


PROFILES = ("claude-hermes", "developer", "librarian", "research", "writer")


class AllProfiles(unittest.TestCase):
    """LIFE-321: the table, `deploy_plugin.sh --all` and `check_profiles.sh`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pack = os.path.join(self.tmp, "pack")
        shutil.copytree(HERMES, self.pack, ignore=shutil.ignore_patterns("__pycache__"))
        self.home = os.path.join(self.tmp, "home")
        for name in PROFILES:
            os.makedirs(os.path.join(self.home, "profiles", name))
            self.config(name, ["claude-subscription-directsdk-experimental"])
        self.config("main", ["advanced-delegation"])
        self.backup = os.path.join(self.tmp, "backup")

    def pdir(self, name):
        return self.home if name == "main" else os.path.join(self.home, "profiles", name)

    def config(self, name, enabled, disabled=()):
        with open(os.path.join(self.pdir(name), "config.yaml"), "w") as handle:
            handle.write("model:\n  default: x\nplugins:\n  clone_timeout_seconds: 300\n  enabled:\n" + "".join("    - %s\n" % e for e in enabled)
                         + ("  disabled:\n" + "".join("    - %s\n" % d for d in disabled) if disabled else "  disabled: []\n") + "agent:\n  x: 1\n")

    def run_script(self, script, *args):
        done = subprocess.run(["bash", os.path.join(self.pack, script), "--home", self.home, *args], capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def deploy(self, *args):
        return self.run_script("deploy_plugin.sh", "--all", *args)

    def enable_everywhere(self):
        for name in PROFILES + ("main",):
            enabled = ["x", "connector-allowlist"]
            self.config(name, enabled)

    def test_the_repo_table_covers_every_known_bot_and_lists_only_safe_names(self):
        with open(os.path.join(HERMES, "connector-allowlists.txt")) as handle:
            rows = {}
            for line in handle:
                line = line.split("#", 1)[0].strip()
                if line:
                    name, _, names = line.partition(":")
                    rows[name.strip()] = [n.strip() for n in names.split(",") if n.strip()]
        self.assertEqual(sorted(rows), sorted(PROFILES + ("main",)))
        for name, allowed in rows.items():
            for item in allowed:
                self.assertRegex(item, r"^(mcp:)?[a-z0-9][a-z0-9_-]{0,63}$", name)
                self.assertNotIn(item, ("robinhood", "plaid", "paypal", "mcp:robinhood"), name)           # nothing financial by default

    def test_all_defaults_to_a_dry_run_and_writes_nothing(self):
        status, out = self.deploy()
        self.assertEqual(status, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn("6 profile(s)", out)
        self.assertIn("18 file(s) would change", out)
        self.assertFalse(os.path.exists(os.path.join(self.home, "plugins")))
        for name in PROFILES:
            self.assertFalse(os.path.exists(os.path.join(self.pdir(name), "plugins")), name)

    def test_apply_all_installs_every_profile_with_its_own_list_and_main_in_the_root_folder(self):
        status, out = self.deploy("--apply", "--backup", self.backup)
        self.assertEqual(status, 0, out)
        self.assertIn("read-back OK", out)
        for name in PROFILES + ("main",):
            dst = os.path.join(self.pdir(name), "plugins", "connector-allowlist")
            for f in ("plugin.yaml", "__init__.py"):
                with open(os.path.join(dst, f)) as got, open(os.path.join(PLUGIN, f)) as want:
                    self.assertEqual(got.read(), want.read(), (name, f))
        with open(os.path.join(self.pdir("developer"), "plugins", "connector-allowlist", "allowlist.txt")) as handle:
            self.assertEqual(handle.read(), "")                                                        # developer: nothing allowed
        with open(os.path.join(self.pdir("main"), "plugins", "connector-allowlist", "allowlist.txt")) as handle:
            self.assertEqual(handle.read(), "notion\n")
        self.assertIn("  hermes plugins enable connector-allowlist", out)
        for name in PROFILES:
            self.assertIn("hermes --profile %s plugins enable connector-allowlist" % name, out)
        self.assertIn("systemctl restart hermes-eval-backend", out)
        for name in PROFILES + ("main",):                                                              # config untouched
            with open(os.path.join(self.pdir(name), "config.yaml")) as handle:
                self.assertNotIn("connector-allowlist", handle.read(), name)
        status, again = self.deploy("--apply", "--backup", os.path.join(self.tmp, "b2"))
        self.assertIn("0 file(s) would change", again)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "b2")))

    def test_an_existing_copy_is_backed_up_per_profile(self):
        self.assertEqual(self.deploy("--apply", "--backup", self.backup)[0], 0)
        table = os.path.join(self.pack, "connector-allowlists.txt")
        with open(table) as handle:
            text = handle.read()
        with open(table, "w") as handle:
            handle.write(text.replace("research: notion", "research: notion, mcp:todoist"))
        status, out = self.deploy("--apply", "--backup", os.path.join(self.tmp, "b2"))
        self.assertEqual(status, 0, out)
        self.assertIn("1 file(s) would change", out)
        with open(os.path.join(self.tmp, "b2", "research", "allowlist.txt")) as handle:
            self.assertEqual(handle.read(), "notion\n")
        with open(os.path.join(self.pdir("research"), "plugins", "connector-allowlist", "allowlist.txt")) as handle:
            self.assertEqual(handle.read(), "notion\nmcp:todoist\n")
        self.assertIn("ROLLBACK research/allowlist.txt", out)

    def test_a_bad_table_is_refused_before_anything_is_written(self):
        table = os.path.join(self.pack, "connector-allowlists.txt")
        os.makedirs(os.path.join(self.home, "profiles", "extra"))
        for line, needle in (("extra: notion, *\n", "is not an allowed name"), ("no colon here\n", "bad table line"), ("../x: notion\n", "is not a profile name"),
                             ("writer: notion\n", "listed twice"), ("ghost: notion\n", "no profile folder")):
            with open(os.path.join(HERMES, "connector-allowlists.txt")) as handle:
                text = handle.read()
            with open(table, "w") as handle:
                handle.write(text + line)
            status, out = self.deploy("--apply", "--backup", self.backup)
            self.assertEqual(status, 1, (line, out))
            self.assertIn(needle, out)
        self.assertFalse(os.path.exists(os.path.join(self.pdir("librarian"), "plugins")))
        self.assertFalse(os.path.exists(self.backup))

    def test_all_cannot_be_mixed_with_one_profile_and_none_means_nothing_allowed(self):
        status, out = self.run_script("deploy_plugin.sh", "--all", "--profile", "librarian")
        self.assertEqual(status, 1, out)
        self.assertIn("cannot be combined", out)
        status, out = self.run_script("deploy_plugin.sh", "--profile", "main", "--allow", "none", "--apply", "--backup", self.backup)
        self.assertEqual(status, 0, out)
        with open(os.path.join(self.home, "plugins", "connector-allowlist", "allowlist.txt")) as handle:
            self.assertEqual(handle.read(), "")

    # ---- the auditor
    def test_the_auditor_fails_on_a_bare_host_and_names_every_profile(self):
        status, out = self.run_script("check_profiles.sh")
        self.assertEqual(status, 1, out)
        for name in PROFILES + ("main",):
            self.assertIn("  %s: FAIL" % name, out)
        self.assertIn("is not in plugins.enabled", out)
        self.assertIn("FAIL: at least one profile is NOT limited correctly", out)

    def test_the_auditor_passes_only_when_everything_is_installed_and_enabled(self):
        self.assertEqual(self.deploy("--apply", "--backup", self.backup)[0], 0)
        status, out = self.run_script("check_profiles.sh")
        self.assertEqual(status, 1, out)                                                               # installed but not yet enabled
        self.enable_everywhere()
        status, out = self.run_script("check_profiles.sh")
        self.assertEqual(status, 0, out)
        self.assertIn("PASS: every profile is limited", out)
        self.assertIn("developer: OK (may use: nothing)", out)
        self.assertIn("librarian: OK (may use: notion)", out)

    def test_a_brand_new_bot_is_reported_as_unprotected(self):
        self.assertEqual(self.deploy("--apply", "--backup", self.backup)[0], 0)
        self.enable_everywhere()
        os.makedirs(os.path.join(self.home, "profiles", "newbot"))
        status, out = self.run_script("check_profiles.sh")
        self.assertEqual(status, 1, out)
        self.assertIn("newbot: FAIL", out)
        self.assertIn("UNPROTECTED", out)
        self.assertIn("librarian: OK", out)

    def test_the_auditor_catches_a_disabled_plugin_a_changed_file_and_a_wrong_list(self):
        self.assertEqual(self.deploy("--apply", "--backup", self.backup)[0], 0)
        self.enable_everywhere()
        self.config("writer", ["connector-allowlist"], disabled=["connector-allowlist"])
        with open(os.path.join(self.pdir("research"), "plugins", "connector-allowlist", "__init__.py"), "a") as handle:
            handle.write("# tampered\n")
        with open(os.path.join(self.pdir("librarian"), "plugins", "connector-allowlist", "allowlist.txt"), "w") as handle:
            handle.write("notion\nmcp:robinhood\n")
        status, out = self.run_script("check_profiles.sh")
        self.assertEqual(status, 1, out)
        self.assertIn("writer: FAIL; plugin is in plugins.disabled", out)
        self.assertIn("research: FAIL; __init__.py differs from the repo", out)
        self.assertIn("librarian: FAIL; allowlist.txt holds [mcp:robinhood notion] but the table says [notion]", out)
        self.assertIn("main: OK", out)

    def test_the_auditor_fails_when_a_table_profile_has_no_folder_and_writes_nothing(self):
        self.assertEqual(self.deploy("--apply", "--backup", self.backup)[0], 0)
        self.enable_everywhere()
        shutil.rmtree(self.pdir("writer"))
        before = sorted(os.listdir(self.home))
        status, out = self.run_script("check_profiles.sh")
        self.assertEqual(status, 1, out)
        self.assertIn("writer: FAIL (in the table but no profile folder)", out)
        self.assertEqual(sorted(os.listdir(self.home)), before)

    def test_both_scripts_have_valid_syntax(self):
        for name in ("deploy_plugin.sh", "check_profiles.sh"):
            self.assertEqual(subprocess.run(["bash", "-n", os.path.join(HERMES, name)]).returncode, 0, name)


if __name__ == "__main__":
    unittest.main()
