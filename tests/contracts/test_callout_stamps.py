"""every Daily Report card shows when it was last updated IN ITS HEADING, in Central Time, WITH THE DATE: `<Title> (11/13
· 3:31 PM CT)`.
"""
import datetime as dt
import pathlib
import re
import unittest
from zoneinfo import ZoneInfo

from lifeos.agenda import card as agenda
from lifeos.amazon import card as amazon
from lifeos.bills import card as bills
from lifeos.imessage import comms_card
from lifeos.jira import card as jira
from lifeos.megibow import render as megibow
from lifeos.oraclecloud import card as oracle
from lifeos.physmail import card as physmail
from lifeos.platform import ct_stamp, router

NOW = dt.datetime(2026, 11, 12, 13, 35, tzinfo=ZoneInfo("America/Chicago"))
CLOCK = r"(?:[1-9]|1[0-2])/(?:[1-9]|[12]\d|3[01]) · (?:[1-9]|1[0-2]):\d{2} [AP]M CT"
ROOT = pathlib.Path(__file__).resolve().parents[2]


def heading_form(title):
    """`<Title> (11/12 · 1:35 PM CT)`"""
    return re.compile(rf"^{re.escape(title)} \({CLOCK}(?:, [^()0-9:]+ unread)?\)$")


def oracle_heading():
    return f"{oracle.TITLE} ({ct_stamp.when(NOW)})"




# region (or card name) -> (the card's title, a function returning the heading the card writes for a fixed time)
HEADINGS = {
    router.JIRA_REGION: (router.JIRA_REGION, lambda: jira.heading_text(NOW)),
    router.CALENDAR_REGION: (router.CALENDAR_REGION, lambda: agenda._heading_text({"taken_at": NOW.isoformat()}, False)),
    router.BILLS_REGION: (router.BILLS_REGION, lambda: bills.heading_text({"taken_at": NOW.isoformat()}, False)),
    router.AMAZON_REGION: (router.AMAZON_REGION, lambda: amazon.heading_text(NOW)),
    router.MAIL_ALERTS_REGION: (router.MAIL_ALERTS_REGION, lambda: physmail.heading_text(NOW)),
    router.COMMS_REGION: (router.COMMS_REGION, lambda: comms_card.render(comms_card.UNAVAILABLE, NOW)[1]),
    router.ORACLE_REGION: (router.ORACLE_REGION, oracle_heading),
    "MegIBOW": ("MegIBOW", lambda: re.match(r"MegIBOW \([^)]*\)", megibow.status(NOW)).group(0)),
}
# Not a Daily Report card heading, with the reason
EXEMPT = {
    router.DCC_REGION: "the Command Center's stamp is in its heading, `ChatGPT · Daily Command Center · Tue 11/24 · 11:02 PM CT` (lifeos/dcc/card.py heading_text: weekday and date included), checked by the DCC tests",
    router.HIRING_REGION: "LIFE-973: a live Job Ledger view inside its callout; V7 writes no heading or stamp there",
    router.RECRUITER_OUTREACH_REGION: "LIFE-973: a live Recruiters view; V7 writes only its gray count line (lifeos/recruiters/outreach_card.py), which carries no stamp",
    router.REPO_REGION: "the Weekly LIFE OS Review page, not the Daily Report (lifeos/platform/branches.py)",
}

# Files that write a time with `CT` but are not a Daily Report card heading (each has its own form, with a date.
NOT_A_CARD_HEADING = {
    "lifeos/platform/ct_stamp.py": "the formatter itself",
    "lifeos/platform/branches.py": "the Weekly LIFE OS Review line (`Fri, 11/13 · 3:31 PM CT`: weekday and date)",
    "lifeos/platform/scheduled_jobs.py": "the scheduled-jobs table in a report, with its own date",
    "lifeos/sources/pipeline/job_page_outreach.py": "the Outreach heading on a job page (`Updated 10/20 @ 9:40 PM CT`, a date already; not a Daily Report card)",
    "lifeos/hiring/models.py": "it only READS the stamp back out of the status line (accepting the new form and the time-only form); it builds none",
    "lifeos/hiring/render.py": "the clock of a meeting row (`Today 3:30 PM CT`), not a heading stamp",
    "lifeos/jobs/backlog.py": "a release report title with its date",
    "lifeos/sources/apps/outreach_app.py": "a conversation time with its date",
    "lifeos/sources/apps/resume_app.py": "a résumé caption with its date",
    "lifeos/recruiters/scan.py": "the scan time a run records for its next run (`scan_at=`), not a heading",
    "lifeos/jobs/fit/cloud.py": "a window reset time with its date",
}


class EveryCardHeadingShowsWhenItWasUpdated(unittest.TestCase):
    def test_each_card_heading_is_the_title_then_the_update_time_in_ct(self):
        for region, (title, render) in HEADINGS.items():
            with self.subTest(region=region):
                heading = render()
                self.assertRegex(heading, heading_form(title), heading)
                self.assertEqual(len(re.findall(r"\d{1,2}:\d{2} [AP]M", heading)), 1, "the time is shown once: " + heading)  # never a second time (the old `mail:...)
                self.assertNotIn("mail:", heading)
                self.assertNotRegex(heading, r"\b(UTC|CST|CDT|GMT)\b")  # always "CT", never a zone name or UTC

    def test_every_router_region_has_a_rule_so_a_new_card_cannot_skip_the_stamp(self):
        covered = set(HEADINGS) | set(EXEMPT)
        self.assertEqual(sorted(set(router.OWNERS) - covered), [], "a region has no heading rule: add it to HEADINGS (or EXEMPT, with the reason) in this test")

    def test_the_degraded_stale_and_unread_forms_keep_the_title_and_the_date_and_clock_in_the_heading(self):
        self.assertEqual(jira.heading_text(NOW, NOW), "JIRA Execution (STALE · last accepted 11/12 · 1:35 PM CT)")
        self.assertEqual(amazon.heading_text(NOW, True), "Amazon Orders (DEGRADED at 11/12 · 1:35 PM CT)")
        self.assertEqual(physmail.heading_text(NOW, True), "Mail Alerts (DEGRADED at 11/12 · 1:35 PM CT)")
        self.assertEqual(bills.heading_text({"taken_at": NOW.isoformat()}, True), "Bills: This Week (STALE · last accepted 11/12 · 1:35 PM CT)")
        self.assertEqual(agenda._heading_text({"taken_at": NOW.isoformat()}, True), "Calendar (STALE · last accepted 11/12 · 1:35 PM CT)")
        self.assertTrue(megibow.status(NOW, degraded=True).startswith("MegIBOW (DEGRADED at 11/12 · 1:35 PM CT)"))
        self.assertEqual(oracle_heading().split(" (")[1][:len("11/12 · 1:35 PM CT")], "11/12 · 1:35 PM CT")

    def test_one_formatter_builds_the_stamp_for_every_form_and_a_new_year_and_zone_are_right(self):
        self.assertEqual(ct_stamp.when(dt.datetime(2026, 11, 13, 21, 31, tzinfo=dt.timezone.utc)), "11/13 · 3:31 PM CT")  # UTC in, Central out
        self.assertEqual(ct_stamp.when(dt.datetime(2026, 7, 4, 17, 5, tzinfo=dt.timezone.utc)), "7/4 · 12:05 PM CT")  # CDT, no leading zero on the day or the hour
        self.assertEqual(ct_stamp.when(dt.datetime(2026, 11, 13, 0, 5)), "11/13 · 12:05 AM CT")  # a naive moment is already Central
        self.assertEqual(ct_stamp.when(dt.datetime(2026, 11, 13, 12, 0, tzinfo=ZoneInfo("America/Chicago"))), "11/13 · 12:00 PM CT")
        self.assertEqual(ct_stamp.when(dt.datetime(2026, 1, 5, 9, 7, tzinfo=ZoneInfo("America/Chicago"))), "1/5 · 9:07 AM CT")  # month and day with no leading zero, no year
        self.assertEqual(ct_stamp.when(NOW), "11/12 · 1:35 PM CT")
        self.assertFalse(hasattr(ct_stamp, "updated"), "the word Updated is gone: one formatter, `when`")

    def test_readers_accept_the_new_form_and_every_older_one(self):
        """no card flashes DEGRADED or STALE after the deploy: the stamp reads the same in the new form and in both older
        ones.
        """
        for text, expected in (("Calendar (11/13 · 3:31 PM CT)", (11, 13, 3, 31, "PM")), ("Updated 1/5 · 12:05 AM CT · x", (1, 5, 12, 5, "AM")), ("Updated Nov 13 · 3:31 PM CT", (11, 13, 3, 31, "PM")),
                               ("Calendar (Updated 1:18 PM CT)", (None, None, 1, 18, "PM")), ("Updated Foo 13 · 3:31 PM CT", None), ("13/2 · 3:31 PM CT", None), ("no stamp here", None), ("", None)):
            self.assertEqual(ct_stamp.read(text), expected, text)

    def test_no_card_builds_a_stamp_without_the_shared_formatter(self):
        """A line that writes `Updated {`, `DEGRADED at {`, `last accepted {` or `Redrawn {` must take its moment from
        ct_stamp
        """
        spoken = re.compile(r"(?:Updated|DEGRADED at|last accepted|Redrawn) ?\{")
        by_hand = re.compile(r"%-?I:%M %p[^\n]*[\"'] ?CT\b|%-?I:%M %p[^\n]*\bCT[\"'\)\}]")
        offenders = []
        for path in sorted((ROOT / "lifeos").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(("lifeos/edge/", "lifeos/oraclecloud/oracle")) or rel in NOT_A_CARD_HEADING:
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                code = line.split("  # ")[0]
                if (spoken.search(code) and "ct_stamp." not in code) or (by_hand.search(code) and "ct_stamp." not in code):
                    offenders.append(f"{rel}:{number}")
        self.assertEqual(offenders, [], "build the stamp with lifeos.platform.ct_stamp.when")

    def test_the_hand_built_stamp_this_test_hunts_is_caught(self):
        for wrong in ('return f"{TITLE} (Updated {now.strftime(\'%-I:%M %p\')} CT)"', 'f"DEGRADED at {clock}"', 'x = f"last accepted {taken}"'):
            self.assertTrue(re.search(r"(?:Updated|DEGRADED at|last accepted|Redrawn) ?\{|%-?I:%M %p[^\n]*\bCT[\"'\)\}]", wrong), wrong)

    def test_the_negative_controls_the_old_and_wrong_forms_are_caught(self):
        form = heading_form("Mail Alerts")
        self.assertRegex("Mail Alerts (11/12 · 1:35 PM CT)", form)
        for wrong in ("Mail Alerts (Updated 11/12 · 1:35 PM CT)", "Mail Alerts (Nov 12 · 1:35 PM CT)", "Mail Alerts (Updated Nov 12 · 1:35 PM CT)", "Mail Alerts (11/12/2026 · 1:35 PM CT)", "Mail Alerts (01/12 · 1:35 PM CT)", "Mail Alerts (1/12 · 01:35 PM CT)", "Mail Alerts (Updated 1:35 PM CT)", "Mail Alerts (Updated Nov 12 1:35 PM CT)", "Mail Alerts (Updated Nov 12, 1:35 PM CT)", "Mail Alerts (11/12 · 1:35 PM CT, mail: new mail 1:35 PM CT)", "Mail Alerts", "Updated 1:35 PM CT · 11 waiting", "Mail Alerts (Updated 1:35 PM CT, mail: new mail 1:35 PM CT)", "Mail Alerts · Updated 1:35 PM CT",
                      "Mail Alerts (Updated 1:35 PM CST)", "Mail Alerts (Updated 1:35 PM UTC)", "Mail Alerts (Last refreshed 1:35 PM CT)"):
            self.assertNotRegex(wrong, form, wrong)


if __name__ == "__main__":
    unittest.main()
