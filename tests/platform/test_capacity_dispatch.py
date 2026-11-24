"""LIFE-957: the hourly tick starts the AI Capacity card when GitHub's own cron has not (it ran the card only every 5 to 10 hours). The tick step's real script runs here against a fake `gh`."""
import os
import pathlib
import stat
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
FAKE_GH = """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$*" in
  *capacity-card.yml/runs*)                                   # LAST: minutes since the last card run, "none" for no history, "FAIL" for an unreadable one
    [ "$LAST" = "FAIL" ] && exit 1
    [ "$LAST" = "none" ] && exit 0
    python3 -c 'import sys, datetime as d; print((d.datetime.now(d.timezone.utc) - d.timedelta(minutes=int(sys.argv[1]))).strftime("%Y-%m-%dT%H:%M:%SZ"), end="")' "$LAST" ;;
  *domains.yml/dispatches*) exit "${DOMAINS_EXIT:-0}" ;;
  *capacity-card.yml/dispatches*) exit "${CARD_EXIT:-0}" ;;
esac
"""


def step():
    steps = yaml.safe_load((ROOT / ".github/workflows/hourly.yml").read_text())["jobs"]["prep"]["steps"]
    found = [s for s in steps if str(s.get("name", "")).startswith("LIFE-775 - start the early domains run")]
    assert len(found) == 1
    return found[0]


def run_step(last, **extra):
    """-> (exit code, the gh calls the step made)."""
    with tempfile.TemporaryDirectory() as tmp:
        gh = pathlib.Path(tmp, "gh")
        gh.write_text(FAKE_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        log = pathlib.Path(tmp, "calls")
        env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}", "GH_LOG": str(log), "LAST": last, "GITHUB_REPOSITORY": "o/r", "GITHUB_REF_NAME": "main", "GITHUB_RUN_ID": "7", **extra}
        done = subprocess.run(["bash", "-e", "-c", step()["run"]], env=env, capture_output=True, text=True, timeout=60)
        return done.returncode, log.read_text().splitlines() if log.exists() else []


def started_card(calls):
    return [c for c in calls if "capacity-card.yml/dispatches" in c]


class TickStartsTheCard(unittest.TestCase):
    def test_a_card_run_older_than_forty_minutes_is_started_by_the_tick_following_v7_live(self):
        code, calls = run_step("75")
        self.assertEqual(code, 0)
        self.assertEqual(len(started_card(calls)), 1)
        self.assertIn("inputs[tick]=true", started_card(calls)[0])
        self.assertNotIn("inputs[live]", started_card(calls)[0])                     # the tick never forces a live write: the card follows V7_LIVE

    def test_a_recent_run_from_the_cron_is_not_doubled(self):
        code, calls = run_step("6")                                               # the :55 cron fired five minutes before this tick
        self.assertEqual((code, started_card(calls)), (0, []))

    def test_no_history_or_an_unreadable_one_starts_the_card(self):
        self.assertEqual(len(started_card(run_step("none")[1])), 1)
        self.assertEqual(len(started_card(run_step("FAIL")[1])), 1)

    def test_the_early_domains_run_is_still_started_and_its_failure_still_fails_the_step(self):
        code, calls = run_step("75")
        self.assertEqual(len([c for c in calls if "domains.yml/dispatches" in c]), 1)
        code, calls = run_step("75", DOMAINS_EXIT="3")
        self.assertEqual(code, 3)                                                    # a failed domains dispatch is still reported by the step (it tolerates it as before) ...
        self.assertEqual(len(started_card(calls)), 1)                                # ... and does not stop the card from starting

    def test_a_failed_card_dispatch_costs_only_a_stale_card(self):
        code, _ = run_step("75", CARD_EXIT="1")
        self.assertEqual(code, 0)

    def test_the_card_workflow_takes_the_tick_flag_and_a_tick_run_follows_v7_live_not_the_manual_box(self):
        doc = yaml.safe_load((ROOT / ".github/workflows/capacity-card.yml").read_text())
        on = doc.get("on") or doc.get(True)
        self.assertEqual(on["workflow_dispatch"]["inputs"]["tick"]["default"], False)
        live_step = next(s for s in doc["jobs"]["capacitycard"]["steps"] if str(s.get("name", "")).startswith("Plain switches"))
        self.assertIn("!inputs.tick", live_step["with"]["live_mode"])
        self.assertEqual(on["schedule"], [{"cron": "55 * * * *"}])                 # the cron stays as a backup


if __name__ == "__main__":
    unittest.main()
