"""LIFE-688: the daily write-conflict count in the watchdog's check-in (gate.write_conflicts, gate.conflict_line) and its rule.

A write conflict is one of NOTION_HTTP_409, a page-walk 404 (with or without a job's prefix) or PROTECTED_REGION_CHANGED surfacing in a domains-run job log in the last 24 hours; NOTION_HTTP_429 is a rate limit, counted apart.
Counts per job by fixed code only; the logs are fake archives built here, so no test touches the network."""
import datetime as dt
import io
import pathlib
import unittest
import zipfile
from unittest import mock

from lifeos.platform import gate

NOW = dt.datetime(2026, 11, 15, 13, 17, tzinfo=dt.timezone.utc)


def stamp(hours_ago):
    return (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def domains_run(rid, hours_ago, conclusion="success"):
    return {"id": rid, "display_title": gate.DOMAINS_TITLE, "conclusion": conclusion, "created_at": stamp(hours_ago + 0.2), "updated_at": stamp(hours_ago)}


def archive(**jobs):
    """A run's log archive: one folder per job, one step file each, plus the run's own system.txt."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:                                 # the shape of a real archive: `<n>_<job>.txt` holds the whole job log, `<job>/system.txt` is the runner's
        bundle.writestr("system.txt", "NOTION_HTTP_409 in a file that is not a job")
        for number, (job, text) in enumerate(jobs.items()):
            bundle.writestr(f"{number}_{job}.txt", text)
            bundle.writestr(f"{job}/system.txt", "NOTION_HTTP_409 in the runner's own file")
    return buffer.getvalue()


CLEAN = archive(jira="jira-card: {\"written\": 1}", mail="mail-card: {\"status\": \"fresh\"}")


class Reading(unittest.TestCase):
    def test_codes_are_counted_per_job_and_once_per_run_whatever_the_prefix(self):
        found = gate.conflict_codes_in(archive(
            hiring="HIRING_PAGE_WALK_NOTION_HTTP_404:column\nHIRING_PAGE_WALK_NOTION_HTTP_404:column",
            recruiters="RECRUITER_OUTREACH_PAGE_WALK_NOTION_HTTP_404:toggle\nRECRUITER-OUTREACH FAILED: RECRUITER_OUTREACH_PROTECTED_REGION_CHANGED",
            mail="MAIL-CARD FAILED: NOTION_HTTP_409", jira="JIRA-CARD FAILED: NOTION_HTTP_429", bills="bills-card: ok"))
        self.assertEqual(found, {("hiring", "PAGE_WALK_NOTION_HTTP_404"): 1, ("recruiters", "PAGE_WALK_NOTION_HTTP_404"): 1, ("recruiters", "PROTECTED_REGION_CHANGED"): 1,
                                 ("mail", "NOTION_HTTP_409"): 1, ("jira", "NOTION_HTTP_429"): 1})
        self.assertEqual(sorted({job for job, _ in found}), ["hiring", "jira", "mail", "recruiters"])      # the runner's system.txt files are not jobs and their codes are not counted

    def test_a_clean_run_has_no_codes_and_no_content_is_kept(self):
        self.assertEqual(gate.conflict_codes_in(CLEAN), {})


class Counting(unittest.TestCase):
    def count(self, runs, logs, **kw):
        kw.setdefault("count_from", None)                                                   # these tests look at a whole 24-hour window; the start of counting has its own test
        return gate.write_conflicts(runs, NOW, logs=logs, **kw)

    def test_conflicts_and_rate_limits_are_counted_per_job_over_the_last_24_hours_only(self):
        logs = {1: archive(hiring="X_PAGE_WALK_NOTION_HTTP_404", mail="NOTION_HTTP_409"), 2: archive(hiring="PROTECTED_REGION_CHANGED", imessage="NOTION_HTTP_429"),
                3: archive(agenda="NOTION_HTTP_409"), 4: archive(bills="NOTION_HTTP_409"), 5: archive(jira="NOTION_HTTP_409")}
        runs = [domains_run(1, 1), domains_run(2, 3, "failure"), domains_run(3, 23), domains_run(4, 25), domains_run(5, 2, "cancelled")]      # 4 is older than a day, 5 never finished
        seen = []
        result = self.count(runs, lambda rid, limit: seen.append(rid) or logs[rid])
        self.assertEqual((result["conflicts"], result["rate_limits"]), ({"hiring": 2, "mail": 1, "agenda": 1}, {"imessage": 1}))
        self.assertEqual((sorted(seen), result["runs"], result["read"], result["unreadable"], result["capped"]), ([1, 2, 3], 3, 3, 0, False))
        self.assertEqual(result["codes"][("hiring", "PAGE_WALK_NOTION_HTTP_404")], 1)

    def test_no_conflicts_is_a_count_of_zero(self):
        result = self.count([domains_run(1, 1), domains_run(2, 2)], lambda rid, limit: CLEAN)
        self.assertEqual((result["conflicts"], result["rate_limits"], result["read"]), ({}, {}, 2))

    def test_an_unreadable_log_degrades_the_count_without_raising_and_keeps_the_other_runs(self):
        def logs(rid, limit):
            if rid == 2:
                raise OSError("GitHub said no: secret text that must never be kept")
            return archive(mail="NOTION_HTTP_409")
        result = self.count([domains_run(1, 1), domains_run(2, 2), domains_run(3, 3)], logs)
        self.assertEqual((result["conflicts"], result["unreadable"], result["read"]), ({"mail": 2}, 1, 2))
        line = gate.conflict_line(result)
        self.assertIn("Count incomplete: WRITE_CONFLICT_LOGS_UNREADABLE for 1 of 3 runs.", line)
        self.assertNotIn("secret", line)                                                    # only the fixed code, never the error's text

    def test_a_corrupt_archive_is_one_unreadable_run(self):
        result = self.count([domains_run(1, 1), domains_run(2, 2)], lambda rid, limit: b"not a zip file" if rid == 1 else archive(jira="NOTION_HTTP_409"))
        self.assertEqual((result["conflicts"], result["unreadable"]), ({"jira": 1}, 1))

    def test_the_run_cap_the_byte_cap_and_the_time_cap_stop_the_reading_and_say_so(self):
        runs = [domains_run(n, n) for n in range(1, 8)]
        calls = []
        with mock.patch.object(gate, "CONFLICT_RUNS_MAX", 3):
            result = self.count(runs, lambda rid, limit: calls.append(rid) or CLEAN)
        self.assertEqual((calls, result["runs"]), ([1, 2, 3], 3))                          # newest first, three runs at most
        with mock.patch.object(gate, "CONFLICT_BYTES_MAX", len(CLEAN)):
            calls.clear()
            result = self.count(runs, lambda rid, limit: calls.append(rid) or CLEAN)
        self.assertEqual((calls, result["capped"], result["read"]), ([1], True, 1))        # the first archive used the whole byte budget
        ticks = iter(range(0, 10_000, 40))
        calls.clear()
        result = self.count(runs, lambda rid, limit: calls.append(rid) or CLEAN, clock=lambda: next(ticks))
        self.assertTrue(result["capped"] and len(calls) < 7)                                # 90 seconds of budget at 40 seconds a look
        self.assertIn("WRITE_CONFLICT_CAP_REACHED after", gate.conflict_line(result))

    def test_an_archive_bigger_than_the_byte_budget_is_an_unreadable_run_not_a_guess(self):
        def logs(rid, limit):
            if len(CLEAN) > limit:
                raise ValueError(gate.CONFLICT_LOGS_UNREADABLE)
            return CLEAN
        with mock.patch.object(gate, "CONFLICT_BYTES_MAX", 10):
            result = self.count([domains_run(1, 1)], logs)
        self.assertEqual((result["unreadable"], result["read"]), (1, 0))


class CountingStartsAtTheDecision(unittest.TestCase):
    def test_runs_before_the_decision_are_history_and_the_line_says_the_window_is_shorter(self):
        logs = {1: archive(recruiters="X_PAGE_WALK_NOTION_HTTP_404"), 2: archive(hiring="NOTION_HTTP_409")}
        runs = [domains_run(1, 9), domains_run(2, 3)]                                        # run 1 finished 9 hours before NOW (4:17 AM UTC), run 2 three hours before
        now = dt.datetime(2026, 11, 15, 13, 17, tzinfo=dt.timezone.utc)                       # 7:17 AM CT, the first check-in after the decision (12:05 AM CT = 06:05 UTC)
        result = gate.write_conflicts(runs, now, logs=lambda rid, limit: logs[rid])
        self.assertEqual((result["conflicts"], result["runs"], result["since"] is not None), ({"hiring": 1}, 1, True))      # only the run after the decision
        line = gate.conflict_line(result)
        self.assertIn("Counted since the writer-chain decision (Nov 15 2026, 12:05 AM CT).", line)
        self.assertTrue(line.startswith("Notion write conflicts in the last 24 h: 1 (hiring 1)"))

    def test_a_full_24_hour_window_after_the_decision_is_not_clipped_and_says_nothing_about_it(self):
        later = dt.datetime(2026, 11, 17, 13, 17, tzinfo=dt.timezone.utc)
        run_ = {"id": 1, "display_title": gate.DOMAINS_TITLE, "conclusion": "success", "created_at": "2026-11-17T12:00:00Z", "updated_at": "2026-11-17T12:10:00Z"}
        result = gate.write_conflicts([run_], later, logs=lambda rid, limit: CLEAN)
        self.assertEqual((result["since"], result["read"]), (None, 1))
        self.assertNotIn("Counted since", gate.conflict_line(result))


class TheLineAndTheRule(unittest.TestCase):
    def test_zero_conflicts_shows_the_count_and_no_rule(self):
        line = gate.conflict_line({"runs": 3, "read": 3, "unreadable": 0, "capped": False, "conflicts": {}, "rate_limits": {}, "codes": {}})
        self.assertEqual(line, "Notion write conflicts in the last 24 h: 0; rate limits: 0.")
        self.assertNotIn("rule", line)

    def test_any_conflict_says_so_plainly_with_the_rule_and_rate_limits_stay_apart(self):
        line = gate.conflict_line({"runs": 3, "read": 3, "unreadable": 0, "capped": False, "conflicts": {"recruiters": 1, "hiring": 2}, "rate_limits": {"jira": 1}, "codes": {}})
        self.assertEqual(line, "Notion write conflicts in the last 24 h: 3 (hiring 2, recruiters 1); rate limits: 1 (jira 1). "
                               "rule: any write conflict moves the Daily Report writers to the full chain (option A, LIFE-688).")

    def test_rate_limits_alone_are_not_a_conflict_and_do_not_trigger_the_rule(self):
        line = gate.conflict_line({"runs": 1, "read": 1, "unreadable": 0, "capped": False, "conflicts": {}, "rate_limits": {"imessage": 2}, "codes": {}})
        self.assertEqual(line, "Notion write conflicts in the last 24 h: 0; rate limits: 2 (imessage 2).")

    def test_runs_that_could_not_be_listed_say_unavailable_under_its_fixed_code(self):
        self.assertEqual(gate.conflict_line(None), "Notion write conflicts: unavailable (WRITE_CONFLICT_COUNT_UNAVAILABLE).")


class TheDocsSayTheSame(unittest.TestCase):
    def test_the_rule_and_the_codes_are_in_both_docs_exactly_as_the_code_has_them(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        for name in ("docs/platform/notion-writers.md", "docs/platform/health-model.md"):
            text = (root / name).read_text()
            for needed in (gate.CONFLICT_RULE, gate.CONFLICT_LOGS_UNREADABLE, gate.CONFLICT_CAP_REACHED, gate.CONFLICT_UNAVAILABLE, "Notion write conflicts in the last 24 h: N (hiring a, recruiters b); rate limits: M.",
                           "Counted since the writer-chain decision (Nov 15 2026, 12:05 AM CT)."):
                self.assertIn(needed, text, f"{name} must carry: {needed}")
        notion_writers = (root / "docs/platform/notion-writers.md").read_text()
        for number in (str(gate.CONFLICT_RUNS_MAX), str(gate.CONFLICT_SECONDS_MAX), "40 million"):
            self.assertIn(number, notion_writers)
        self.assertEqual(gate.CONFLICT_BYTES_MAX, 40_000_000)
        for code in gate.CONFLICT_CODES + (gate.RATE_LIMIT_CODE,):
            self.assertIn(code, notion_writers)


class InTheCheckin(unittest.TestCase):
    RUNS = [{"id": 1, "display_title": "tick", "conclusion": "success", "created_at": stamp(0.5), "updated_at": stamp(0.4)}]
    LINE = "Notion write conflicts in the last 24 h: 0; rate limits: 0."

    def test_with_no_conflicts_nothing_else_in_the_checkin_changes(self):
        title, body = gate.health(self.RUNS, [], NOW)
        with_line = gate.health(self.RUNS, [], NOW, conflicts=self.LINE)
        self.assertEqual(with_line, (title, body + " " + self.LINE))                        # same title, same body, one added line
        self.assertEqual(gate.health(self.RUNS, [], NOW, conflicts=""), (title, body))      # no line: exactly the old check-in

    def test_a_conflict_or_an_unavailable_count_never_changes_ok_or_degraded(self):
        ok = gate.health(self.RUNS, [], NOW)[0]
        for line in (gate.conflict_line(None), "Notion write conflicts in the last 24 h: 4 (hiring 4). rule: ..."):
            self.assertEqual(gate.health(self.RUNS, [], NOW, conflicts=line)[0], ok)

    def watch_at_checkin(self, domain_runs, write_conflicts):
        now, pushed = dt.datetime(2026, 11, 15, 13, 17, tzinfo=dt.timezone.utc), []
        hourly = {"workflow_runs": [{"id": 1, "display_title": "tick", "conclusion": "success", "created_at": stamp(0.5), "updated_at": stamp(0.4)}]}

        def api(path, *a, **k):
            if "workflows/domains" in path:
                if domain_runs is None:
                    raise OSError("GitHub unavailable")
                return {"workflow_runs": domain_runs}
            if "workflows/hourly" in path:
                return hourly
            return {}
        with mock.patch.object(gate, "_api", side_effect=api), mock.patch.object(gate, "write_conflicts", write_conflicts):
            gate.watch(now, push=lambda *a: pushed.append(a) or True)
        return [p for p in pushed if p[3] == "daily_check"]

    def test_the_watchdog_adds_the_line_to_the_checkin_and_a_failure_of_the_count_never_stops_it(self):
        good = {"runs": 1, "read": 1, "unreadable": 0, "capped": False, "conflicts": {"hiring": 1}, "rate_limits": {}, "codes": {}}
        (sent,) = self.watch_at_checkin([domains_run(1, 1)], lambda runs, now: good)
        self.assertIn("Notion write conflicts in the last 24 h: 1 (hiring 1); rate limits: 0. rule: any write conflict moves the Daily Report writers to the full chain (option A, LIFE-688).", sent[1])
        raising = mock.Mock(side_effect=RuntimeError("anything"))
        (sent,) = self.watch_at_checkin([domains_run(1, 1)], raising)
        self.assertIn("WRITE_CONFLICT_COUNT_UNAVAILABLE", sent[1])
        self.assertIn("V7 daily check", sent[0])                                           # the check-in still went out
        (sent,) = self.watch_at_checkin(None, raising)                                     # the runs could not be listed at all
        self.assertIn("WRITE_CONFLICT_COUNT_UNAVAILABLE", sent[1])
        raising.assert_called_once()                                                       # not even asked when there is nothing to read


if __name__ == "__main__":
    unittest.main()
