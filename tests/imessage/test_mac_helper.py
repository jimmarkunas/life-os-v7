import hashlib
import json
import os
import pathlib
import plistlib
import re
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAC = ROOT / "lifeos/imessage/mac"
INSTALL = (MAC / "install.sh").read_text()
LAUNCHER = (MAC / "launcher.c").read_text()
TOOL = ROOT / "lifeos/imessage/attention_mcp.py"


def var(name):
    return re.search(r'^%s="([^"]*)"' % name, INSTALL, re.M).group(1)


class Installer(unittest.TestCase):
    def test_pinned_hashes_match_the_reviewed_sources(self):
        for name, path in (("SHA_TOOL", TOOL), ("SHA_LAUNCHER", MAC / "launcher.c"), ("SHA_PLIST", MAC / "ai.lifeos.imessage-attention.plist")):
            self.assertEqual(var(name), hashlib.sha256(path.read_bytes()).hexdigest(), name + " is stale: re-pin install.sh")

    def test_only_local_read_only_mechanics(self):
        for banned in (r"\bssh\b", r"\btailscale\b", r"openclaw\.json", r"\bopenclaw (config|nodes|approvals|gateway)\b", r"\btccutil\b", r"system\.run", r"\bnc\b", r"\bsocat\b", r"launchctl\s+load",
                       r"^\s*curl\b[^\n]* -(d|X|T|F|-data\S*|-upload-file)\b"):
            self.assertIsNone(re.search(banned, INSTALL, re.M), banned)
        calls = re.findall(r"/usr/bin/sqlite3 (\S+)", INSTALL)
        self.assertTrue(calls)
        self.assertEqual({c for c in calls if c != "/usr/bin/csreq"}, {"-readonly"})       # every database call is read-only

    def test_paths_agree_across_installer_launcher_and_agent(self):
        agent = plistlib.loads((MAC / "ai.lifeos.imessage-attention.plist").read_bytes())
        directory = var("DIR")
        self.assertEqual(agent["Program"], directory + "/lifeos-imessage-attention")
        self.assertEqual(agent["Label"], var("LABEL"))
        self.assertIn('#define TOOL "%s/attention_mcp.py"' % directory, LAUNCHER)


class LaunchAgent(unittest.TestCase):
    def test_on_demand_owner_only_unix_socket_and_nothing_else(self):
        agent = plistlib.loads((MAC / "ai.lifeos.imessage-attention.plist").read_bytes())
        self.assertEqual(set(agent), {"Label", "Program", "ProgramArguments", "inetdCompatibility", "Sockets", "ProcessType"})
        self.assertEqual(agent["ProgramArguments"], [agent["Program"]])
        self.assertEqual(agent["inetdCompatibility"], {"Wait": False})
        self.assertEqual(agent["Sockets"], {"Listener": {"SockType": "stream", "SockFamily": "Unix", "SockPathName": "__SOCKET__", "SockPathMode": 0o600}})


class Launcher(unittest.TestCase):
    def test_takes_no_caller_input_and_runs_one_fixed_isolated_command(self):
        self.assertIn("int main(void)", LAUNCHER)
        self.assertNotIn("getenv", LAUNCHER)
        self.assertNotRegex(LAUNCHER, r"\b(system|popen|execvp|execlp)\s*\(")
        self.assertIn('char *argv[] = {"/usr/bin/python3", "-I", "-S", "-B", TOOL, NULL};', LAUNCHER)


class SocketMode(unittest.TestCase):
    def test_tool_serves_one_connection_as_launchd_hands_it_over(self):
        parent, child = socket.socketpair()
        env = dict(os.environ, HOME=tempfile.mkdtemp())
        proc = subprocess.Popen([sys.executable, "-I", str(TOOL)], stdin=child, stdout=child, stderr=subprocess.PIPE, env=env)
        child.close()
        parent.sendall(b"\xff\xfe garbage\n" + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "imessage_attention", "arguments": {}}}).encode() + b"\n")
        parent.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = parent.recv(4096)
            if not chunk:
                break
            data += chunk
        parent.close()
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertEqual(proc.stderr.read(), b"")
        proc.stderr.close()
        (reply,) = [json.loads(x) for x in data.decode().splitlines()]
        state = reply["result"]["structuredContent"]
        self.assertEqual((state["health"], state["attention"], state["count"]), ("DISCONNECTED", "UNKNOWN", None))


class Gate(unittest.TestCase):
    """The installer's own `gate`, run in bash: only exactly N PASS lines and nothing else lets a phase continue."""

    def gate(self, phase, required, results):
        defs = INSTALL[:INSTALL.index("\npreflight\ncase")]
        script = defs + '\nsummary() { say "$1"; }\nRESULTS=(' + " ".join("'%s'" % r for r in results) + ")\ngate %s %d\necho CONTINUED\n" % (phase, required)
        run = subprocess.run(["bash", "-c", script, "install.sh"], capture_output=True, text=True, timeout=10)
        return run.returncode, run.stdout

    def test_all_required_checks_passing_continues(self):
        self.assertEqual(self.gate("post", 2, ["a: PASS", "b: PASS"]), (0, "CONTINUED\n"))

    def test_a_failure_stops_with_rollback_and_manual_revoke(self):
        code, out = self.gate("post", 2, ["a: PASS", "b: FAIL"])
        self.assertEqual(code, 1)
        self.assertIn("RESULT: LOCAL PROOF FAILED (post: 1 of 2 required checks passed; 1 failed or unverified)", out)
        self.assertIn('install.sh" uninstall', out)
        self.assertIn("revoke the helper's Full Disk Access by hand", out)
        self.assertNotIn("CONTINUED", out)
        self.assertNotIn("PASSED", out)

    def test_unverified_or_missing_checks_also_stop(self):
        self.assertEqual(self.gate("post", 2, ["a: PASS", "b: UNVERIFIED"])[0], 1)
        self.assertEqual(self.gate("post", 3, ["a: PASS", "b: PASS"])[0], 1)

    def test_an_unexpected_stop_after_install_still_reports_failure_and_rollback(self):
        defs = INSTALL[:INSTALL.index("\npreflight\ncase")]
        run = subprocess.run(["bash", "-c", defs + "\nINSTALLED=1\nfalse\necho UNREACHED\n", "install.sh"], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("RESULT: LOCAL PROOF FAILED (an installer step stopped unexpectedly", run.stdout)
        self.assertIn('install.sh" uninstall', run.stdout)
        self.assertNotIn("UNREACHED", run.stdout)

    def test_pre_grant_failure_says_do_not_grant(self):
        code, out = self.gate("pre", 1, ["a: FAIL"])
        self.assertEqual(code, 1)
        self.assertIn("Do NOT grant Full Disk Access", out)

    def test_success_is_claimed_only_after_the_final_gate(self):
        for flow, required in (("install", 27), ("verify", 18)):
            line = re.search(r"^  %s\) (.*)$" % flow, INSTALL, re.M).group(1)
            self.assertLess(line.index("gate post %d" % required), line.index("LOCAL PROOF PASSED (%d of %d" % (required, required)))
        self.assertIn("gate pre 15", INSTALL)


class AgentCheck(unittest.TestCase):
    """The installer's structured LaunchAgent check, run in bash against the rendered template: comments cannot fail it, real keys cannot pass it."""
    SOCK = "/tmp/example home/Library/Application Support/LifeOS/imessage-attention.sock"

    def check(self, text):
        path = os.path.join(tempfile.mkdtemp(), "agent.plist")
        with open(path, "w") as f:
            f.write(text.replace("__SOCKET__", self.SOCK))
        defs = INSTALL[:INSTALL.index("\npreflight\ncase")].replace("/usr/bin/python3 -I - ", sys.executable + " -I - ")
        script = defs + '\nAGENT="$1"; SOCK="$2"\nagent_exact && echo EXACT || echo REJECTED\n'
        return subprocess.run(["bash", "-c", script, "install.sh", path, self.SOCK], capture_output=True, text=True, timeout=10).stdout.strip()

    def test_the_published_template_passes_even_though_its_comment_names_forbidden_keys(self):
        template = (MAC / "ai.lifeos.imessage-attention.plist").read_text()
        self.assertIn("No RunAtLoad, KeepAlive, StartInterval", template)
        self.assertEqual(self.check(template), "EXACT")

    def test_any_real_extra_key_or_changed_socket_is_rejected(self):
        template = (MAC / "ai.lifeos.imessage-attention.plist").read_text()
        top = "    <key>ProcessType</key>"
        for extra in ("<key>RunAtLoad</key><true/>", "<key>KeepAlive</key><true/>", "<key>StartInterval</key><integer>60</integer>"):
            self.assertEqual(self.check(template.replace(top, "    " + extra + "\n" + top)), "REJECTED", extra)
        for old, new in (("<integer>384</integer>", "<integer>438</integer>"), ("<string>Unix</string>", "<string>IPv4</string>"),
                         ("<false/>", "<true/>"), ("<key>SockPathMode</key>", "<key>SockNodeName</key><string>0.0.0.0</string><key>SockPathMode</key>"),
                         ("lifeos-imessage-attention</string>\n    <key>ProgramArguments", "other</string>\n    <key>ProgramArguments")):
            self.assertIn(old, template)
            self.assertEqual(self.check(template.replace(old, new, 1)), "REJECTED", new)
        self.assertEqual(self.check("not a plist"), "REJECTED")


class CodeHash(unittest.TestCase):
    """The installer's CDHash parser over codesign's display output."""

    def parse(self, output):
        program = re.search(r"/usr/bin/awk -F= '([^']*)'", INSTALL).group(1)
        return subprocess.run(["awk", "-F=", program], input=output, capture_output=True, text=True, timeout=10).stdout.strip()

    def test_reads_the_cdhash_line_only(self):
        digest = "0123456789ABCDEF0123456789abcdef01234567"
        verbose = "Executable=/x\nIdentifier=ai.lifeos.imessage-attention\nCandidateCDHash sha256=ffff\nCandidateCDHashFull sha256=eeee\nCDHash=%s\nSignature=adhoc\n" % digest
        self.assertEqual(self.parse(verbose), digest.lower())

    def test_missing_or_malformed_hash_is_empty(self):
        self.assertEqual(self.parse("Executable=/x\nIdentifier=ai.lifeos.imessage-attention\nSignature=adhoc\n"), "")
        self.assertEqual(self.parse("CDHash=not-a-hash\n"), "")

    def test_a_missing_hash_fails_identity_before_any_grant(self):
        self.assertIn('[ -n "$CDHASH" ] && record "helper code hash read from its signature: $CDHASH" PASS || record "helper code hash read from its signature (none found)" FAIL', INSTALL)
        self.assertNotIn("codesign -dv ", INSTALL)                     # plain -dv does not print CDHash


class GrantDetection(unittest.TestCase):
    """The installer's awk rules over `client|auth_value` rows of the macOS Full Disk Access list."""

    def awk(self, kind, rows):
        program = re.findall(r"/usr/bin/awk -F'\|' (?:-v id=\"\$app_id\" -v app=\"\$OPENCLAW_APP/\" )?'([^']*)'", INSTALL)[0 if kind == "openclaw" else 1]
        return subprocess.run(["awk", "-F|", "-v", "id=ai.example.openclaw-mac", "-v", "app=/Applications/OpenClaw.app/", program], input="\n".join(rows) + "\n",
                              capture_output=True, text=True, timeout=10).returncode

    def test_openclaw_grant_is_caught_by_bundle_id_path_or_name(self):
        self.assertEqual(self.awk("openclaw", ["com.apple.Terminal|2", "/usr/local/libexec/lifeos-imessage/lifeos-imessage-attention|2"]), 0)
        for row in ("ai.example.openclaw-mac|2", "/Applications/OpenClaw.app/Contents/Resources/node-worker/arm64/bin/node|2", "com.vendor.OpenClaw|2"):
            self.assertEqual(self.awk("openclaw", ["com.apple.Terminal|2", row]), 1, row)
        self.assertEqual(self.awk("openclaw", ["ai.example.openclaw-mac|0"]), 0)

    def test_any_python_grant_is_caught(self):
        self.assertEqual(self.awk("python", ["com.apple.Terminal|2"]), 0)
        for row in ("/usr/bin/python3|2", "com.apple.python3|2", "/Library/Developer/CommandLineTools/usr/bin/python3|2", "org.python.python|3"):
            self.assertEqual(self.awk("python", [row]), 1, row)
        self.assertEqual(self.awk("python", ["/usr/bin/python3|0"]), 0)


class InstallerProbe(unittest.TestCase):
    """The installer's own client, run against a launchd-like server: one tool process per connection, the connection as its stdin/stdout."""

    def test_probe_passes_every_check_against_the_real_tool_without_messages_access(self):
        script = re.search(r"<<'PY'\n(.*?)\nPY\n", INSTALL, re.S).group(1)
        home = tempfile.mkdtemp()
        path = os.path.join(home, "s.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(4)
        server.settimeout(10)
        env = dict(os.environ, HOME=home)
        probe = subprocess.Popen([sys.executable, "-I", "-", path, "pre"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        probe.stdin.write(script)
        probe.stdin.close()
        for _ in range(2):                                            # the probe opens exactly two connections
            conn, _ = server.accept()
            subprocess.run([sys.executable, "-I", str(TOOL)], stdin=conn, stdout=conn, env=env, timeout=10, check=True)
            conn.close()
        server.close()
        out = json.loads(probe.stdout.read())
        probe.stdout.close()
        self.assertEqual(probe.wait(timeout=10), 0)
        self.assertEqual(len(out["checks"]), 6)
        self.assertEqual({status for _, status in out["checks"]}, {"PASS"}, out["checks"])
        self.assertEqual((out["state"]["health"], out["state"]["attention"], out["state"]["count"]), ("DISCONNECTED", "UNKNOWN", None))


if __name__ == "__main__":
    unittest.main()
