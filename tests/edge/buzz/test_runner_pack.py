"""LIFE-590: the Oracle install pack for the renamed Buzz modules, built from the repo and run through the REAL runner on a temp tree with fake systemctl and curl (no sudo, no host)."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lifeos.edge.runner import runner
from tests.edge.runner.test_runner import FakeSystemd

ROOT = Path(__file__).resolve().parents[3]
BUILDER = ROOT / "lifeos" / "edge" / "buzz" / "oracle" / "build_pack.py"
OLD_UNIT = "[Service]\nExecStart=/usr/bin/python3 -s -B -m lifeos.buzz_mcp\n"
FAKE_SYSTEMCTL = r"""#!/bin/bash
# fake systemctl for the pack test: `restart` records the module the installed unit names into the fake process's cmdline
P="$(cd "$(dirname "$0")/../.." && pwd)"
echo "$@" >> "$P/calls.log"
case "$1" in
  restart) mod=$(grep -o 'lifeos\.buzz[a-z_.]*' "$P/etc/systemd/system/lifeos-buzz-mcp.service" | head -1)
           target=$(readlink "$P/opt/lifeos-buzz-mcp/current"); echo "$target" > "$P/running-release"
           [ -e "$P/fail-restart" ] && [ "$target" != "releases/c423a4434c4c" ] && exit 1
           mkdir -p "$P/proc/4242"; printf 'python3\0-m\0%s\0' "${FORCE_MODULE:-$mod}" > "$P/proc/4242/cmdline"
           [ -e "$P/wrong-module" ] && [ "$target" != "releases/c423a4434c4c" ] && printf 'python3\0-m\0lifeos.other\0' > "$P/proc/4242/cmdline"
           exit 0 ;;
  show) echo 4242 ;;
  is-active) echo active ;;
  *) exit 0 ;;
esac
"""
FAKE_CURL = r"""#!/bin/bash
P="$(cd "$(dirname "$0")/../.." && pwd)"
target=$(cat "$P/running-release" 2>/dev/null)
if [ -e "$P/bad-http" ] && [ "$target" != "releases/c423a4434c4c" ]; then printf 500; else printf 401; fi
"""


class PackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.prefix = self.tmp / "fake-root"
        base = self.prefix / "opt" / "lifeos-buzz-mcp"
        (base / "releases" / "c423a4434c4c").mkdir(parents=True)
        (base / "current").symlink_to("releases/c423a4434c4c")
        (self.prefix / "etc" / "systemd" / "system").mkdir(parents=True)
        (self.prefix / "etc" / "systemd" / "system" / "lifeos-buzz-mcp.service").write_text(OLD_UNIT)
        (self.prefix / "usr" / "bin").mkdir(parents=True)
        for name, body in (("systemctl", FAKE_SYSTEMCTL), ("curl", FAKE_CURL), ("sleep", "#!/bin/bash\nexit 0\n")):
            path = self.prefix / "usr" / "bin" / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.pack = self.tmp / "pack"
        subprocess.run([sys.executable, str(BUILDER), str(self.pack), "--root-prefix", str(self.prefix)], cwd=ROOT, check=True, capture_output=True)
        self.state, self.inbox = str(self.tmp / "state"), str(self.tmp / "lifeos-pack-{key}")
        shutil.copytree(self.pack, self.inbox.format(key="LIFE-590"))
        self.host = runner.Host(state=self.state, inbox=self.inbox, require_root=False, systemctl=FakeSystemd(str(self.tmp)))
        self.sha8 = runner.pack_hash(self.inbox.format(key="LIFE-590"))[:8]

    def release(self):
        return (self.pack / "files" / "RELEASE").read_text().strip()

    def run_pack(self):
        return runner.execute("LIFE-590", self.sha8, self.host)

    def current(self):
        return os.readlink(self.prefix / "opt" / "lifeos-buzz-mcp" / "current")

    def unit(self):
        return (self.prefix / "etc" / "systemd" / "system" / "lifeos-buzz-mcp.service").read_text()

    def test_the_pack_is_valid_and_holds_exactly_the_files_the_comment_names(self):
        manifest = runner.load_manifest(str(self.pack))
        self.assertEqual(manifest["services"], ["lifeos-buzz-mcp.service"])
        shipped = sorted(str(p.relative_to(self.pack / "files" / "tree")) for p in (self.pack / "files" / "tree").rglob("*") if p.is_file())
        self.assertEqual(shipped, ["lifeos/__init__.py", "lifeos/buzz/__init__.py", "lifeos/buzz/auth.py", "lifeos/buzz/mcp.py", "lifeos/buzz/nip98.py", "lifeos/buzz/relay.py"])
        self.assertIn("-m lifeos.buzz.mcp", (self.pack / "files" / "lifeos-buzz-mcp.service").read_text())
        self.assertEqual(len(manifest["files"]), 1 + len(shipped))

    def test_a_good_run_switches_the_release_and_the_unit_and_reads_back(self):
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("ok", None), result)
        self.assertEqual(self.current(), "releases/" + self.release())
        self.assertIn("-m lifeos.buzz.mcp", self.unit())
        readback = " ".join(result["readback"]) if isinstance(result["readback"], list) else str(result["readback"])
        for want in ("switched=yes", "service_active=active", "process_module=lifeos.buzz.mcp", "previous_release_present=yes", "http_mcp=401"):
            self.assertIn(want, readback)
        self.assertTrue((self.prefix / "opt" / "lifeos-buzz-mcp" / "releases" / "c423a4434c4c").is_dir())

    def assert_back_on_the_old_release(self, result, code):
        self.assertEqual((result["outcome"], result["code"]), ("fail", code), result)
        self.assertEqual(self.current(), "releases/c423a4434c4c")
        self.assertEqual(self.unit(), OLD_UNIT)
        self.assertEqual((self.prefix / "running-release").read_text().strip(), "releases/c423a4434c4c")          # the service was restarted on the old release
        self.assertFalse((self.prefix / "opt" / "lifeos-buzz-mcp" / "releases" / self.release() / "lifeos" / "buzz" / "mcp.py").exists())       # the runner removed the new files

    def test_a_failed_restart_puts_the_old_unit_and_release_back(self):
        (self.prefix / "fail-restart").touch()
        self.assert_back_on_the_old_release(self.run_pack(), "E_STEP_2")

    def test_the_service_not_answering_401_puts_everything_back(self):
        (self.prefix / "bad-http").touch()
        self.assert_back_on_the_old_release(self.run_pack(), "E_STEP_2")

    def test_the_process_not_running_the_new_module_puts_everything_back(self):
        (self.prefix / "wrong-module").touch()
        self.assert_back_on_the_old_release(self.run_pack(), "E_STEP_2")

    def test_a_missing_previous_release_stops_before_anything_changes(self):
        shutil.rmtree(self.prefix / "opt" / "lifeos-buzz-mcp" / "releases" / "c423a4434c4c")
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"))
        self.assertEqual(self.unit(), OLD_UNIT)

    def test_the_pack_touches_no_other_unit_and_names_no_credential(self):
        text = "".join(p.read_text() for p in (self.pack / "steps").glob("*.sh"))
        self.assertNotIn("openclaw", text.lower())
        self.assertNotIn("hermes", text.lower())
        calls = [line for line in text.splitlines() if "SYSTEMCTL\" restart" in line]
        self.assertTrue(all("$SVC" in line for line in calls))
        for word in ("password", "token", "secret", "credential", "BEGIN"):
            self.assertNotIn(word, text)


class ScratchPackTests(unittest.TestCase):
    """The harmless first pack (ticket key LIFE-310): one scratch file, read back and removed."""

    def test_the_scratch_pack_is_valid_and_leaves_nothing_behind_when_it_succeeds(self):
        pack = ROOT / "lifeos" / "edge" / "runner" / "scratch-pack"
        manifest = runner.load_manifest(str(pack))
        self.assertEqual((manifest["ticket"], manifest["files"], manifest["services"]), ("LIFE-310", ["/tmp/lifeos-runner-scratch.txt"], []))
        with tempfile.TemporaryDirectory() as folder:
            inbox = os.path.join(folder, "lifeos-pack-{key}")
            shutil.copytree(pack, inbox.format(key="LIFE-310"))
            host = runner.Host(state=os.path.join(folder, "state"), inbox=inbox, require_root=False, systemctl=FakeSystemd(folder))
            result = runner.execute("LIFE-310", runner.pack_hash(inbox.format(key="LIFE-310"))[:8], host)
        self.assertEqual((result["outcome"], result["code"]), ("ok", None), result)
        self.assertFalse(os.path.exists("/tmp/lifeos-runner-scratch.txt"))


if __name__ == "__main__":
    unittest.main()
