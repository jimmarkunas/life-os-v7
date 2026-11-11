"""LIFE-330: the Oracle ticker (lifeos/edge/ticker) - the tick request, fixed codes, the fail-closed watch, the timers' minutes and the installer's reach."""
import configparser
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from lifeos.edge.ticker import ticker
from lifeos.platform import codes, gate

ROOT = Path(__file__).resolve().parents[3]
ORACLE = ROOT / "lifeos" / "edge" / "ticker" / "oracle"
TOKEN = "github_pat_NOTAREALTOKEN_0123456789abcdef"
TOPIC = "not-a-real-topic-123"


class Reply:
    def __init__(self, status, body=b""):
        self.status, self.body = status, body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeGitHub:
    """Records requests; `answers` maps a path suffix to an HTTP status (with a JSON body for 200) or an exception."""
    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        for suffix, answer in self.answers.items():
            if request.full_url.endswith(suffix):
                if isinstance(answer, Exception):
                    raise answer
                status, body = answer if isinstance(answer, tuple) else (answer, None)
                if status >= 400:
                    raise urllib.error.HTTPError(request.full_url, status, "x", {}, None)
                return Reply(status, json.dumps(body).encode() if body is not None else b"")
        raise AssertionError(f"unexpected request {request.full_url}")


SWITCHES = {f"variables/{n}": (200, {"name": n, "value": v}) for n, v in
            (("ALERTS_ENABLED", "on"), ("ALERTS_MUTED_UNTIL", ""), ("ALERT_SETTINGS", '{"v":1}'))}


class Tick(unittest.TestCase):
    def test_the_dispatch_is_exactly_what_cron_job_org_sent(self):
        api = FakeGitHub({"hourly.yml/dispatches": 204})
        self.assertEqual(ticker.tick(TOKEN, api), ("TICK_DISPATCHED", 204))
        (req,) = [r for r in api.requests if r.get_method() == "POST"]          # LIFE-568: the run list is read first (a failed read still dispatches)
        self.assertEqual((req.full_url, req.get_method()), ("https://api.github.com/repos/jimmarkunas/life-os-v7/actions/workflows/hourly.yml/dispatches", "POST"))
        self.assertEqual(json.loads(req.data), {"ref": "main", "inputs": {"tick": "true"}})
        self.assertEqual(req.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(req.get_header("Accept"), "application/vnd.github+json")

    def test_a_tick_inside_the_gap_is_not_dispatched_and_one_outside_is(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 11, 6, 17, tzinfo=dt.timezone.utc)
        recent = {"workflow_runs": [{"id": 1, "display_title": "tick", "conclusion": "success", "run_started_at": "2026-11-11T06:00:10Z"}]}
        older = {"workflow_runs": [{"id": 1, "display_title": "tick", "conclusion": "success", "run_started_at": "2026-11-11T05:00:10Z"}]}
        for body, want in ((recent, True), (older, False)):
            api = FakeGitHub({"per_page=15": (200, body), "GATE_MIN_GAP_MINUTES": 404})
            self.assertEqual(ticker.too_soon(TOKEN, api, now), want)
        manual = {"workflow_runs": [{"id": 1, "display_title": "manual", "conclusion": "success", "run_started_at": "2026-11-11T06:00:10Z"}]}
        self.assertFalse(ticker.too_soon(TOKEN, FakeGitHub({"per_page=15": (200, manual), "GATE_MIN_GAP_MINUTES": 404}), now))
        cancelled = {"workflow_runs": [{"id": 1, "display_title": "tick", "conclusion": "cancelled", "run_started_at": "2026-11-11T06:00:10Z"}]}
        self.assertFalse(ticker.too_soon(TOKEN, FakeGitHub({"per_page=15": (200, cancelled), "GATE_MIN_GAP_MINUTES": 404}), now))

    def test_the_repository_gap_variable_is_honoured(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 11, 6, 17, tzinfo=dt.timezone.utc)
        body = {"workflow_runs": [{"id": 1, "display_title": "tick", "conclusion": "success", "run_started_at": "2026-11-11T06:00:10Z"}]}      # 17 minutes ago
        drain = FakeGitHub({"per_page=15": (200, body), "GATE_MIN_GAP_MINUTES": (200, {"name": "GATE_MIN_GAP_MINUTES", "value": "10"})})
        self.assertFalse(ticker.too_soon(TOKEN, drain, now))                       # drain mode: a 10 minute gap lets a 17 minute old tick through

    def test_skipping_dispatches_nothing_and_any_doubt_still_dispatches(self):
        import datetime as dt
        recent = {"workflow_runs": [{"id": 1, "display_title": "tick", "conclusion": "success",
                                     "run_started_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=17)).strftime("%Y-%m-%dT%H:%M:%SZ")}]}
        api = FakeGitHub({"per_page=15": (200, recent), "GATE_MIN_GAP_MINUTES": 404, "hourly.yml/dispatches": 204})
        self.assertEqual(ticker.tick(TOKEN, api), ("TICK_SKIPPED", 200))
        self.assertFalse([r for r in api.requests if r.get_method() == "POST"])
        for unreadable in (500, urllib.error.URLError("down")):
            api = FakeGitHub({"per_page=15": unreadable, "hourly.yml/dispatches": 204})
            self.assertEqual(ticker.tick(TOKEN, api), ("TICK_DISPATCHED", 204))

    def test_every_failure_is_a_fixed_code(self):
        for answer, code in ((401, "TICK_REFUSED"), (403, "TICK_REFUSED"), (404, "TICK_REFUSED"), (422, "TICK_REFUSED"),
                             (500, "TICK_GITHUB_ERROR"), (429, "TICK_GITHUB_ERROR"), (urllib.error.URLError("down"), "TICK_NETWORK")):
            self.assertEqual(ticker.tick(TOKEN, FakeGitHub({"dispatches": answer}))[0], code, answer)


class Disk(unittest.TestCase):
    """LIFE-301: the Hermes disk alarm (80%), run inside the watch; quiet (INFO) and a closing signal when it drops."""
    def usage(self, used, free):
        return lambda path: type("U", (), {"used": used, "free": free, "total": used + free})()

    def test_percent_is_used_over_used_plus_available_like_df(self):
        self.assertEqual(ticker.disk_percent("/x", self.usage(33, 67)), 33)
        self.assertEqual(ticker.disk_percent("/x", self.usage(80, 20)), 80)
        self.assertIsNone(ticker.disk_percent("/x", lambda path: (_ for _ in ()).throw(OSError("gone"))))
        self.assertIsNone(ticker.disk_percent("/x", self.usage(0, 0)))

    def test_signal_opens_at_80_closes_below_and_an_unreadable_mount_changes_nothing(self):
        seen = []
        sync = lambda *a: seen.append(a) or "ok"                                  # noqa: E731
        for used, free, signal in ((79, 21, False), (80, 20, True), (95, 5, True), (34, 66, False)):
            ticker.disk_incident(usage=self.usage(used, free), sync=sync)
            self.assertIs(seen[-1][3], signal, (used, free))
        n = len(seen)
        self.assertIsNone(ticker.disk_incident(usage=lambda path: (_ for _ in ()).throw(OSError("gone")), sync=sync))
        self.assertEqual(len(seen), n)

    def test_the_pushes_are_registered_quiet_and_name_the_percent(self):
        from lifeos.platform import alert_registry
        seen = []
        ticker.disk_incident(usage=self.usage(85, 15), sync=lambda *a: seen.append(a))
        label, title, body, signal, opened, closed = seen[0]
        self.assertEqual((label, signal, opened[3], closed[3]), ("hermes-disk-full", True, "hermes_disk_full", "hermes_disk_full_recovered"))
        self.assertIn("85%", body)
        for key in ("hermes_disk_full", "hermes_disk_full_recovered"):
            self.assertEqual(alert_registry.lookup(key).severity, "info")

    def test_watch_runs_the_disk_check_after_the_watchdog_and_a_disk_failure_never_fails_it(self):
        order = []
        code, _ = ticker.watch(TOKEN, TOPIC, FakeGitHub(SWITCHES), run=lambda **k: order.append("gate"), environ={}, disk=lambda: order.append("disk"))
        self.assertEqual((code, order), ("WATCH_DONE", ["gate", "disk"]))
        def boom():
            raise RuntimeError("x")
        self.assertEqual(ticker.watch(TOKEN, TOPIC, FakeGitHub(SWITCHES), run=lambda **k: None, environ={}, disk=boom)[0], "WATCH_DONE")
        order.clear()
        ticker.watch(TOKEN, TOPIC, FakeGitHub(SWITCHES), run=lambda **k: order.append("gate"), environ={})
        self.assertEqual(order, ["gate"])                                         # an injected run alone never touches the real disk check


class Stall(unittest.TestCase):
    """LIFE-562: the Close release timer writes a marker after 2 hours without a green main head; the watch opens one quiet issue and closes it when the marker is gone."""
    def test_the_marker_opens_the_incident_and_its_absence_closes_it(self):
        seen = []
        self.assertTrue(ticker.stall_incident("/x/auto.stalled", lambda p: True, lambda *a: seen.append(a)))
        self.assertFalse(ticker.stall_incident("/x/auto.stalled", lambda p: False, lambda *a: seen.append(a)))
        self.assertEqual([a[3] for a in seen], [True, False])
        self.assertEqual((seen[0][4][3], seen[0][5][3]), ("deploy_stalled", "deploy_stalled_recovered"))
        self.assertEqual(seen[0][0], "deploy-stalled")

    def test_the_watch_runs_it_and_a_failure_never_fails_the_watchdog(self):
        with mock.patch.object(ticker, "stall_incident", side_effect=RuntimeError("x")) as stall:
            code = ticker.watch(TOKEN, TOPIC, FakeGitHub(SWITCHES), run=lambda **k: None, environ={}, disk=lambda: None)
        self.assertEqual(code[0], "WATCH_DONE")
        stall.assert_called_once()


class Watch(unittest.TestCase):
    def test_the_switches_come_from_the_repository_variables_and_no_checkin_is_sent(self):
        calls, env = [], {}
        code, _ = ticker.watch(TOKEN, TOPIC, FakeGitHub(SWITCHES), run=lambda **k: calls.append(k), environ=env)
        self.assertEqual((code, calls), ("WATCH_DONE", [{"checkin": False}]))
        self.assertEqual((env["ALERTS_ENABLED"], env["ALERT_SETTINGS"], env["GITHUB_REPOSITORY"], env["NTFY_TOPIC"]), ("on", '{"v":1}', "jimmarkunas/life-os-v7", TOPIC))

    def test_an_unset_variable_reads_empty_as_in_actions(self):
        env = {}
        answers = {**SWITCHES, "variables/ALERTS_MUTED_UNTIL": 404}
        self.assertEqual(ticker.watch(TOKEN, TOPIC, FakeGitHub(answers), run=lambda **k: None, environ=env)[0], "WATCH_DONE")
        self.assertEqual(env["ALERTS_MUTED_UNTIL"], "")

    def test_unreadable_switches_stop_before_the_watchdog_runs(self):
        for broken in (403, 500, urllib.error.URLError("down")):
            calls = []
            answers = {**SWITCHES, "variables/ALERTS_ENABLED": broken}
            self.assertEqual(ticker.watch(TOKEN, TOPIC, FakeGitHub(answers), run=lambda **k: calls.append(k), environ={})[0], "TICKER_VARIABLES_UNREADABLE")
            self.assertEqual(calls, [], broken)

    def test_a_watchdog_that_cannot_read_github_is_a_fixed_code(self):
        def boom(**k):
            raise urllib.error.URLError("down")
        self.assertEqual(ticker.watch(TOKEN, TOPIC, FakeGitHub(SWITCHES), run=boom, environ={})[0], "TICKER_WATCH_FAILED")

    def test_gate_watch_without_checkin_sends_no_checkin_in_the_digest_hour(self):
        sent, now = [], gate.dt.datetime(2026, 11, 6, gate.DIGEST_HOUR_UTC, 5, tzinfo=gate.dt.timezone.utc)
        old = gate._api
        gate._api = lambda path, *a, **k: {"workflow_runs": [{"id": 1, "conclusion": "success", "display_title": "tick", "updated_at": "2026-11-06T13:00:00Z"}]} if "workflows/" in path else []
        try:
            gate.watch(now, push=lambda *a: sent.append(a) or True, checkin=False)
            self.assertEqual(sent, [])
            gate.watch(now, push=lambda *a: sent.append(a) or True)
            self.assertEqual(len(sent), 1)                                                 # the GitHub watchdog keeps it
        finally:
            gate._api = old


class Main(unittest.TestCase):
    def run_main(self, argv, creds, answers, run=None):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        for name, value in creds.items():
            Path(folder, name).write_text(value + "\n")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = ticker.main(argv, FakeGitHub(answers), run or (lambda **k: None), {"CREDENTIALS_DIRECTORY": folder})
        return rc, out.getvalue()

    def test_output_is_one_line_with_a_code_and_never_a_credential(self):
        both = {"github": TOKEN, "ntfy": TOPIC}
        for argv, answers, want, rc_want in ((["tick"], {"dispatches": 204}, "TICK_DISPATCHED", 0), (["tick"], {"dispatches": 401}, "TICK_REFUSED", 1),
                                             (["watch"], SWITCHES, "WATCH_DONE", 0), (["dry"], SWITCHES, "TICKER_DRY_OK", 0)):
            rc, out = self.run_main(argv, both, answers)
            self.assertEqual((rc, json.loads(out)["code"]), (rc_want, want), argv)
            self.assertNotIn(TOKEN, out)
            self.assertNotIn(TOPIC, out)

    def test_a_missing_credential_stops(self):
        self.assertEqual(self.run_main(["tick"], {}, {})[0], 1)
        rc, out = self.run_main(["watch"], {"github": TOKEN}, SWITCHES)
        self.assertEqual((rc, json.loads(out)["code"]), (1, "TICKER_NO_CREDENTIAL"))
        self.assertEqual(self.run_main(["bogus"], {}, {})[0], 2)

    def test_every_code_it_prints_is_registered(self):
        printed = set(re.findall(r'"([A-Z][A-Z0-9_]+)"', (ROOT / "lifeos/edge/ticker/ticker.py").read_text())) & set(codes.candidates((ROOT / "lifeos/edge/ticker/ticker.py").read_text()))
        self.assertTrue(printed)
        self.assertEqual(sorted(c for c in printed if c not in codes.CODES and c not in codes.NOT_CODES), [])


def unit(name):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(ORACLE / name)
    return parser


class Units(unittest.TestCase):
    def test_the_tick_runs_hourly_and_the_watch_never_on_the_github_watchdogs_minutes(self):
        self.assertEqual(unit("lifeos-ticker-tick.timer")["Timer"]["OnCalendar"], "*-*-* *:17:00")
        minutes = {int(m) for m in unit("lifeos-ticker-watch.timer")["Timer"]["OnCalendar"].split(":")[1].split(",")}
        self.assertEqual(minutes, {5, 20, 35, 50})
        watchdog = yaml.safe_load((ROOT / ".github/workflows/watchdog.yml").read_text())
        github_minutes = {int(c["cron"].split()[0]) for c in watchdog[True]["schedule"]}
        self.assertEqual(minutes & github_minutes, set())                                 # never both racing to open one incident

    def test_services_take_secrets_only_as_encrypted_credentials(self):
        for name, creds in (("lifeos-ticker-tick.service", {"github"}), ("lifeos-ticker-watch.service", {"github", "ntfy"})):
            text = (ORACLE / name).read_text()
            self.assertEqual({m for m in re.findall(r"^LoadCredentialEncrypted=(\w+):/etc/credstore\.encrypted/lifeos-ticker\.\1$", text, re.M)}, creds, name)
            self.assertNotRegex(text, r"(?m)^Environment")
            self.assertIn("DynamicUser=yes", text)
            self.assertIn("ExecStart=/usr/bin/python3 -I /opt/lifeos-ticker/ticker.py ", text)


class Installer(unittest.TestCase):
    SCRIPT = ORACLE / "install-ticker.sh"

    def test_it_parses(self):
        subprocess.run(["bash", "-n", str(self.SCRIPT)], check=True)

    def test_it_only_removes_or_writes_its_own_paths(self):
        text = self.SCRIPT.read_text()
        self.assertIn("OPT=/opt/lifeos-ticker", text)
        self.assertIn('GH="$CREDS/lifeos-ticker.github"; NT="$CREDS/lifeos-ticker.ntfy"', text)
        for line in re.findall(r"rm -[rf]+ ([^;\n]+)", text):
            for target in line.split():
                self.assertIn(target.strip('"'), ('$UNITS/$u', '$OPT', '$GH', '$NT'), line)

    def test_every_file_it_installs_exists_in_the_repo(self):
        text = self.SCRIPT.read_text()
        units = re.search(r'UNIT_FILES="([^"]+)"', text).group(1).split()
        modules = re.search(r'MODULES="([^"]+)"', text).group(1).split()
        for u in units:
            self.assertTrue((ORACLE / u).is_file(), u)
        for m in modules:
            self.assertTrue((ROOT / "lifeos/platform" / m).is_file(), m)
        self.assertEqual(sorted(p.name for p in ORACLE.glob("lifeos-ticker-*")), sorted(units))

    def test_verify_requires_a_real_timer_run_not_just_a_success_result(self):
        """A never-run service reports Result=success and status 0, so `verify` must also require ExecMainStartTimestamp (LIFE-330, Nov 6 2026)."""
        text = self.SCRIPT.read_text()
        self.assertIn("ExecMainStartTimestamp", text)
        self.assertIn("ran_ok lifeos-ticker-tick.service", text)
        self.assertIn("ran_ok lifeos-ticker-watch.service", text)
        self.assertNotIn('check "last tick succeeded"', text)

    def test_the_copied_modules_are_all_the_watchdog_needs(self):
        """The installed tree is ticker.py plus these modules: every lifeos import they make must be in the list."""
        modules = set(re.search(r'MODULES="([^"]+)"', self.SCRIPT.read_text()).group(1).split())
        for m in modules:
            for dep in re.findall(r"from lifeos\.platform import ([\w, ]+)", (ROOT / "lifeos/platform" / m).read_text()):
                for name in dep.replace(" as registry", "").split(","):
                    self.assertIn(name.strip() + ".py", modules, m)
            self.assertNotRegex((ROOT / "lifeos/platform" / m).read_text(), r"from lifeos\.(?!platform)", m)

    def test_the_tick_timer_and_its_installer_run_the_installed_tree_in_isolation(self):
        """Copy exactly what `install` copies into a temporary /opt layout and run `dry` there with python -I: the imports resolve from the copy."""
        text = self.SCRIPT.read_text()
        modules = re.search(r'MODULES="([^"]+)"', text).group(1).split()
        opt = Path(tempfile.mkdtemp()).resolve()                  # macOS temp is a symlink (/var -> /private/var); the probe prints the resolved path
        self.addCleanup(shutil.rmtree, opt)
        (opt / "lifeos/platform").mkdir(parents=True)
        shutil.copy(ROOT / "lifeos/edge/ticker/ticker.py", opt / "ticker.py")
        (opt / "lifeos/__init__.py").write_text("")
        (opt / "lifeos/platform/__init__.py").write_text("")
        for m in modules:
            shutil.copy(ROOT / "lifeos/platform" / m, opt / "lifeos/platform" / m)
        probe = "import runpy, sys; sys.argv = ['ticker.py']; g = runpy.run_path(sys.argv[0] if False else %r, run_name='probe'); " \
                "import lifeos.platform.gate as gate, lifeos.platform.alerts as alerts; print(gate.__file__)" % str(opt / "ticker.py")
        done = subprocess.run(["python3", "-I", "-c", probe], capture_output=True, text=True, cwd=str(opt))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.strip().startswith(str(opt)), done.stdout)


if __name__ == "__main__":
    unittest.main()
