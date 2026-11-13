"""LIFE-643 row 8: the WhatsApp kill switch, run on a fixture (a fake systemctl for both managers and a fake session folder under a temp dir, through the script's environment hooks). It stops and disables the
fake units, moves the session folders into a mode-700 quarantine without reading them, is idempotent, and `--dry-run` changes nothing. A copy of the script that skips the move fails the same check."""
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from lifeos.edge.runner import runner

ROOT = Path(__file__).resolve().parents[3]
PACK = ROOT / "lifeos" / "edge" / "whatsapp" / "pack_row8"
SCRIPT = PACK / "pack" / "files" / "kill_switch.sh"
SECRET = "NOT-A-REAL-SESSION-SECRET"
FAKE_SYSTEMCTL = """#!/bin/bash
# state = files under $KS_FAKE: active-<unit>, enabled-<unit>
S="$KS_FAKE/$KS_WHO"; mkdir -p "$S"
verb=$1; unit=$2
case "$verb" in
  is-active) [ -e "$S/active-$unit" ] && echo active || { echo inactive; exit 3; } ;;
  is-enabled) [ -e "$S/enabled-$unit" ] && echo enabled || { echo disabled; exit 1; } ;;
  stop) rm -f "$S/active-$unit" ;;
  disable) rm -f "$S/enabled-$unit" ;;
esac
"""
UNITS = ("lifeos-wa-attention.socket", "lifeos-wa-attention.service")


def run_script(script, env, *args):
    return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)


def kill_works(script, fx):
    """True only if the script ends with the units off and the session folders moved into a mode-700 quarantine, with their bytes unchanged."""
    fx.reset()
    done = run_script(script, fx.env)
    quarantine = list((fx.root / "var/lib/lifeos-runner/quarantine").glob("whatsapp-*"))
    return (done.returncode == 0 and not fx.new_state.exists() and not fx.old_state.exists() and len(quarantine) == 1
            and stat.S_IMODE(quarantine[0].stat().st_mode) == 0o700 and (quarantine[0] / "new_state/auth/creds.json").read_text() == SECRET
            and not any(fx.fake.glob("system/*-lifeos-wa-attention.*")) and not any(fx.fake.glob("user/*")))


class Fixture:
    def __init__(self, tmp):
        self.root, self.fake = Path(tmp) / "root", Path(tmp) / "fake"
        self.new_state = self.root / "var/lib/lifeos-wa/.local/state/lifeos-whatsapp"
        self.old_state = self.root / "home/ubuntu/.local/state/lifeos-whatsapp"
        for name in ("system", "user"):
            path = Path(tmp) / ("systemctl-" + name)
            path.write_text(FAKE_SYSTEMCTL.replace("$KS_WHO", name))
            path.chmod(0o755)
        self.env = dict(os.environ, KS_ROOT=str(self.root), KS_SYSTEMCTL=str(Path(tmp) / "systemctl-system"), KS_USER_SYSTEMCTL=str(Path(tmp) / "systemctl-user"), KS_FAKE=str(self.fake), KS_STAMP="1")
        self.reset()

    def reset(self):
        import shutil
        for path in (self.root, self.fake):
            shutil.rmtree(path, ignore_errors=True)
        for state in (self.new_state, self.old_state):
            (state / "auth").mkdir(parents=True)
            (state / "auth/creds.json").write_text(SECRET)
        (self.fake / "system").mkdir(parents=True)
        (self.fake / "user").mkdir(parents=True)
        for unit in UNITS:
            (self.fake / "system" / ("active-" + unit)).touch()
            (self.fake / "system" / ("enabled-" + unit)).touch()
        (self.fake / "user/active-lifeos-whatsapp-attention.service").touch()


class KillSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = Fixture(self.tmp.name)

    def snapshot(self):
        return sorted(str(p.relative_to(self.tmp.name)) for p in Path(self.tmp.name).rglob("*") if "systemctl-" not in p.name)

    def test_it_stops_and_disables_the_units_and_moves_the_session_into_a_700_quarantine_in_well_under_a_minute(self):
        start = time.monotonic()
        done = run_script(SCRIPT, self.fx.env)
        elapsed = time.monotonic() - start
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertLess(elapsed, 10)
        for want in ("lifeos_wa_attention_socket=ok", "lifeos_wa_attention_service=ok", "old_user_unit=ok", "openclaw=skipped_manual", "session_new_state=ok", "session_old_state=ok", "quarantine_mode=700"):
            self.assertIn("READBACK " + want, done.stdout)
        q = self.fx.root / "var/lib/lifeos-runner/quarantine"
        self.assertEqual(stat.S_IMODE(q.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((q / "whatsapp-1").stat().st_mode), 0o700)
        self.assertEqual((q / "whatsapp-1/new_state/auth/creds.json").read_text(), SECRET)
        self.assertFalse(self.fx.new_state.exists() or self.fx.old_state.exists())
        self.assertNotIn(SECRET, done.stdout + done.stderr)

    def test_a_second_run_changes_nothing_and_says_skipped(self):
        run_script(SCRIPT, self.fx.env)
        after_first = self.snapshot()
        done = run_script(SCRIPT, self.fx.env)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(self.snapshot(), after_first)
        for want in ("lifeos_wa_attention_socket=skipped", "lifeos_wa_attention_service=skipped", "old_user_unit=skipped", "session_new_state=skipped", "session_old_state=skipped"):
            self.assertIn("READBACK " + want, done.stdout)

    def test_dry_run_changes_nothing(self):
        before = self.snapshot()
        done = run_script(SCRIPT, self.fx.env, "--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("READBACK session_new_state=would_move", done.stdout)
        self.assertIn("READBACK lifeos_wa_attention_service=would_stop_disable", done.stdout)
        self.assertTrue(self.fx.new_state.exists() and (self.fx.fake / "system/active-lifeos-wa-attention.service").exists())

    def test_a_script_that_skips_the_move_fails_the_check(self):
        self.assertTrue(kill_works(SCRIPT, self.fx))
        mutated = Path(self.tmp.name) / "mutated.sh"
        text = SCRIPT.read_text()
        self.assertIn('mv "$src" "$Q/$name"', text)
        mutated.write_text(text.replace('mv "$src" "$Q/$name"', "true"))
        self.assertFalse(kill_works(mutated, self.fx))

    def test_the_pack_builds_and_passes_the_runners_own_check_and_declares_no_restore(self):
        out = Path(self.tmp.name) / "pack"
        subprocess.run([sys.executable, str(PACK / "build_pack.py"), str(out)], cwd=ROOT, check=True, capture_output=True)
        manifest = runner.load_manifest(str(out))
        self.assertEqual((manifest["steps"], manifest["files"], manifest["services"]), (["steps/10-kill.sh"], [], []))
        done = subprocess.run([sys.executable, str(ROOT / "lifeos" / "edge" / "runner" / "runner.py"), "check", str(out)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)


if __name__ == "__main__":
    unittest.main()
