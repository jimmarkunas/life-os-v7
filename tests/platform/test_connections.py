"""step 4d: the System health database as the record behind the Connections row (its writer, the should-work rule,
Concord, Todoist).
"""
import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from lifeos.platform import connections as c
from tests.oraclecloud.support import SystemPage

CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 11, 25, 9, 0, tzinfo=CT)
UTC = dt.timezone.utc
SRC = "src-id"


def database():
    page = SystemPage()
    for record, health, last, source, workspace in (
            ("health:personal:jira", c.OK, "11/16 · 8:30 PM CT", "Jira", "Personal"), ("health:bytalos:jira", c.OK, "11/16 · 8:30 PM CT", "Jira", "Bytalos"),
            ("health:personal:calendar", c.OK, "11/16 · 8:30 PM CT", "Calendar", "Personal"), ("health:personal:gmail", c.OK, "11/16 · 8:30 PM CT", "Gmail", "Personal"),
            ("health:all:followup", c.OK, "11/16 · 8:30 PM CT", "Followup", "All"), ("health:all:concord", c.NOT_CONNECTED, "—", "Concord", "All"),
            ("health:personal:todoist", c.OK, "11/16 · 8:30 PM CT", "Todoist", "Personal"),
            ("health:tgp:everything", c.NOT_CONNECTED, "—", "Everything (tracked, by design)", "TGP"), ("health:personal:school_outlook", c.NOT_CONNECTED, "—", "School Outlook (admin approval required)", "Personal"),
            ("health:bytalos:asana", c.NOT_CONNECTED, "—", "Asana (view only)", "Bytalos")):
        page.health(record, health, last, source, workspace)
    return page


def passed(at):
    return {"last_ok": at, "failed": False}


GOOD = {"jira": passed(dt.datetime(2026, 11, 25, 14, 40, tzinfo=UTC)), "agenda": passed(dt.datetime(2026, 11, 25, 14, 41, tzinfo=UTC)), "recruiters": passed(dt.datetime(2026, 11, 25, 14, 30, tzinfo=UTC)),
        "followup": passed(dt.datetime(2026, 11, 25, 14, 20, tzinfo=UTC))}


class Rows(unittest.TestCase):
    def test_only_the_rows_this_writer_owns_are_read(self):
        page = database()
        rows = c.read_rows(page, SRC)
        self.assertEqual(len(rows), 10)
        self.assertEqual(next(r for r in rows if r["id"] == "health:bytalos:jira")["workspace"], "Bytalos")

    def test_the_data_source_comes_from_the_setting_never_from_source_and_none_is_refused(self):
        self.assertEqual(c.source_id({"LIFEOS_NOTION_SYSTEM_HEALTH_SOURCE": "abc"}), "abc")
        self.assertEqual(c.source_id({"LIFEOS_NOTION_IDS": '{"SYSTEM_HEALTH_SOURCE": "def"}'}), "def")
        with self.assertRaises(c.ConnectionsError):
            c.read_rows(database(), "")

    def test_the_last_ok_text_reads_back_to_a_central_time(self):
        self.assertEqual(c.last_ok_time("11/16 · 8:30 PM CT", NOW), dt.datetime(2026, 11, 16, 20, 30, tzinfo=CT))
        self.assertEqual(c.last_ok_time("12/31 · 11:59 PM CT", dt.datetime(2027, 1, 1, 0, 5, tzinfo=CT)), dt.datetime(2026, 12, 31, 23, 59, tzinfo=CT))  # a date after now is the year before
        self.assertIsNone(c.last_ok_time("—", NOW))


class Evidence(unittest.TestCase):
    def runs(self, jobs_by_run, titles=None):
        def api(path, token):
            if "/workflows/domains.yml/runs" in path:
                return {"workflow_runs": [{"id": i, "conclusion": "success", "display_title": titles[i] if titles else "domains (after tick)"} for i, _ in enumerate(jobs_by_run)]}
            return {"jobs": jobs_by_run[int(path.split("/runs/")[1].split("/")[0])]}
        return api

    def job(self, name, conclusion, at="2026-11-25T14:40:00Z"):
        return {"name": name, "conclusion": conclusion, "completed_at": at}

    def test_a_job_is_ok_with_the_time_it_last_passed_and_skipped_jobs_say_nothing(self):
        api = self.runs([[self.job("jira", "success"), self.job("agenda", "skipped", None)], [self.job("agenda", "success", "2026-11-25T13:41:00Z"), self.job("jira", "success", "2026-11-25T13:40:00Z")]])
        seen = c.evidence({"GITHUB_REPOSITORY": "o/r"}, api)
        self.assertEqual(seen["jira"], {"last_ok": dt.datetime(2026, 11, 25, 14, 40, tzinfo=UTC), "failed": False})
        self.assertEqual(seen["agenda"], {"last_ok": dt.datetime(2026, 11, 25, 13, 41, tzinfo=UTC), "failed": False})

    def test_a_job_whose_newest_finished_run_failed_is_failing_even_if_an_older_one_passed(self):
        seen = c.evidence({"GITHUB_REPOSITORY": "o/r"}, self.runs([[self.job("outlook", "failure")], [self.job("outlook", "success", "2026-11-25T10:00:00Z")]]))
        self.assertEqual(seen["outlook"], {"last_ok": dt.datetime(2026, 11, 25, 10, 0, tzinfo=UTC), "failed": True})

    def test_a_manual_run_that_failed_never_turns_a_connection_red(self):
        """the System table showed JIRA DOWN although every tick passed."""
        api = self.runs([[self.job("jira", "failure")], [self.job("jira", "success", "2026-11-25T10:00:00Z")]], titles=["domains (manual)", "domains (after tick)"])
        self.assertEqual(c.evidence({"GITHUB_REPOSITORY": "o/r"}, api)["jira"], {"last_ok": dt.datetime(2026, 11, 25, 10, 0, tzinfo=UTC), "failed": False})
        self.assertEqual(c.evidence({"GITHUB_REPOSITORY": "o/r"}, self.runs([[self.job("jira", "success")]], titles=["domains (manual)"])), {})

    def test_an_unreadable_history_is_empty_never_a_guess(self):
        def boom(path, token):
            raise OSError()
        self.assertEqual(c.evidence({"GITHUB_REPOSITORY": "o/r"}, boom), {})
        self.assertEqual(c.evidence({}, boom), {})


class Refresh(unittest.TestCase):
    def test_each_owned_row_takes_its_jobs_health_and_last_ok_and_the_rest_are_left_alone(self):
        page = database()
        rows = c.read_rows(page, SRC)
        updates = c.plan(rows, GOOD, NOW)
        names = {page_id.removeprefix("pg-") for page_id, _ in updates}
        self.assertEqual(names, {"health:personal:jira", "health:bytalos:jira", "health:personal:calendar", "health:personal:gmail", "health:all:followup"})
        self.assertEqual(dict(updates)["pg-health:personal:jira"], {"Health": c.OK, "Last OK": "11/25 · 8:40 AM CT"})
        c.apply(page, updates, c.retired_pages(rows))
        self.assertEqual(page.pages["pg-health:all:concord"]["properties"]["Health"]["select"]["name"], c.NOT_CONNECTED)  # nothing exercises Concord: untouched
        self.assertTrue(page.pages["pg-health:personal:todoist"].get("in_trash"))  # the Todoist row is retired, once
        self.assertFalse(any("todoist" in str(entry) and "Health" in str(entry) for entry in page.log if not entry[2].get("in_trash")))

    def test_a_failed_job_marks_its_row_unavailable_and_keeps_its_last_ok(self):
        rows = c.read_rows(database(), SRC)
        updates = dict(c.plan(rows, {**GOOD, "jira": {"last_ok": None, "failed": True}}, NOW))
        self.assertEqual(updates["pg-health:personal:jira"], {"Health": c.FAILING, "Last OK": "11/16 · 8:30 PM CT"})

    def test_a_second_run_with_the_same_evidence_writes_nothing(self):
        page = database()
        rows = c.read_rows(page, SRC)
        c.apply(page, c.plan(rows, GOOD, NOW), [])
        again = c.read_rows(page, SRC)
        self.assertEqual(c.plan(again, GOOD, NOW), [])


class Summary(unittest.TestCase):
    def rows_after(self, seen):
        page = database()
        rows = c.read_rows(page, SRC)
        return c.merged(rows, c.plan(rows, seen, NOW))

    def test_concord_counts_as_should_work_and_shows_down_while_off_by_design_rows_and_todoist_are_not_counted(self):
        summary = c.summary(self.rows_after(GOOD), NOW)
        self.assertEqual((summary["total"], summary["connected"], summary["down"], summary["off"]), (6, 5, ["Concord"], 3))
        self.assertIsNone(summary["stale_since"])

    def test_a_row_that_says_ok_but_was_not_refreshed_for_two_hours_is_not_connected_and_reports_how_stale(self):
        summary = c.summary(self.rows_after({}), NOW)
        self.assertEqual((summary["connected"], sorted(summary["down"])[:2]), (0, ["Bytalos Jira", "Calendar"]))
        self.assertEqual(summary["stale_since"], dt.datetime(2026, 11, 16, 20, 30, tzinfo=CT))

    def test_exactly_two_hours_old_is_still_connected(self):
        at = (NOW - dt.timedelta(hours=2)).astimezone(UTC)
        summary = c.summary(self.rows_after({**GOOD, "jira": passed(at)}), NOW)
        self.assertEqual(summary["connected"], 5)

    def test_no_rows_is_none(self):
        self.assertIsNone(c.summary([], NOW))


if __name__ == "__main__":
    unittest.main()
