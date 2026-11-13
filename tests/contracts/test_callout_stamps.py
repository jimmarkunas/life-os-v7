"""LIFE-609 / LIFE-660: every Daily Report card shows when it was last updated IN ITS HEADING, in Central Time, WITH THE DATE: `<Title> (Updated Nov 13 · 3:31 PM CT)` (Jim, Nov 13 2026: "Why the fuck isn't
the date showing up"; the stamp in the heading, the same form as Calendar's; not a body line, not a second time, no `mail:` clause). Each card's own heading function is called with a fixed time and its
output must match. A region added to the router without a rule here fails; a card whose heading lacks the stamp or the date fails; a stamp only in the body line fails; a card that builds a stamp
without `lifeos.platform.ct_stamp` (the one formatter) fails. Names and times only."""
import datetime as dt
import json
import pathlib
import re
import unittest
from zoneinfo import ZoneInfo

from lifeos.agenda import card as agenda
from lifeos.amazon import card as amazon
from lifeos.bills import card as bills
from lifeos.hiring import models as hiring_models, render as hiring_render
from lifeos.imessage import comms_card
from lifeos.jira import card as jira
from lifeos.jobs.fit import capacity_card
from lifeos.megibow import render as megibow
from lifeos.oraclecloud import card as oracle
from lifeos.physmail import card as physmail
from lifeos.platform import ct_stamp, router
from lifeos.recruiters import outreach_card

NOW = dt.datetime(2026, 11, 12, 13, 35, tzinfo=ZoneInfo("America/Chicago"))
CLOCK = r"[A-Z][a-z]{2} \d{1,2} · \d{1,2}:\d{2} [AP]M CT"                                          # the date, a middle dot, the time: `Nov 12 · 1:35 PM CT`
ROOT = pathlib.Path(__file__).resolve().parents[2]


def heading_form(title):
    """`<Title> (Updated Nov 12 · 1:35 PM CT)`; a mail source that was not read may be named after the time (`, Outlook personal unread`)."""
    return re.compile(rf"^{re.escape(title)} \(Updated {CLOCK}(?:, [^()0-9:]+ unread)?\)$")


def oracle_heading():
    raw = {"schema": oracle.SCHEMA, "observed_at": "2026-11-12T19:34:30Z", "cost": {"amount": 0, "currency": "USD"}, "cpu_24h_pct": 5, "memory_pct": 50, "disk_pct": 40,
           "a1_cores": {"used": 2, "available": 0}, "reclaim": "NONE", "services": {key: True for key, _ in oracle.SERVICES}, "chatgpt": {"left_5h": 100, "left_week": 64},
           "backup": {"ok": True, "code": "ok", "age_h": 3}}
    return oracle.render(oracle.validate(json.dumps(raw), NOW), NOW)[1]


def hiring_heading():
    text = hiring_render.render({"rows": [], "reasons": []}, None, NOW)[0]
    return hiring_models.heading_text(hiring_models.split_status(text)[0])


def capacity_heading():
    return re.match(r"AI Capacity \([^)]*\)", capacity_card.render({"pools": [], "unavailable": [], "safety_cap": None}, NOW)[0].split("\n")[0]).group(0)          # the callout's first line, up to the heading's closing bracket


# region (or card name) -> (the card's title, a function returning the heading the card writes for a fixed time)
HEADINGS = {
    router.JIRA_REGION: (router.JIRA_REGION, lambda: jira.heading_text(NOW)),
    router.CALENDAR_REGION: (router.CALENDAR_REGION, lambda: agenda._heading_text({"taken_at": NOW.isoformat()}, False)),
    router.BILLS_REGION: (router.BILLS_REGION, lambda: bills.heading_text({"taken_at": NOW.isoformat()}, False)),
    router.AMAZON_REGION: (router.AMAZON_REGION, lambda: amazon.heading_text(NOW)),
    router.MAIL_ALERTS_REGION: (router.MAIL_ALERTS_REGION, lambda: physmail.heading_text(NOW)),
    router.HIRING_REGION: (router.HIRING_REGION, hiring_heading),
    router.COMMS_REGION: (router.COMMS_REGION, lambda: comms_card.render(comms_card.UNAVAILABLE, NOW)[1]),
    router.ORACLE_REGION: (router.ORACLE_REGION, oracle_heading),
    router.RECRUITER_OUTREACH_REGION: (router.RECRUITER_OUTREACH_REGION, lambda: outreach_card.heading(NOW)),
    "MegIBOW": ("MegIBOW", lambda: re.match(r"MegIBOW \([^)]*\)", megibow.status(NOW)).group(0)),
    "AI Capacity": ("AI Capacity", capacity_heading),
}
# Not a Daily Report card heading, with the reason:
EXEMPT = {
    router.ATTENTION_REGION: "LIFE-645: V7 retired the card that wrote it; the callout stays on the page, protected, and nothing stamps it",
    router.DCC_REGION: "the Command Center's stamp is the page's first line (lifeos/dcc/header.py, `Friday, Nov 13 · Updated 1:35 PM CT`: it already carries the weekday and date), checked by the DCC tests",
    router.REPO_REGION: "the Weekly LIFE OS Review page, not the Daily Report (lifeos/platform/branches.py)",
}

# Files that write a time with `CT` but are not a Daily Report card heading (each has its own form, with a date already, or is a row time, a receipt or a proof):
NOT_A_CARD_HEADING = {
    "lifeos/platform/ct_stamp.py": "the formatter itself",
    "lifeos/platform/branches.py": "the Weekly LIFE OS Review line (`Updated Fri Nov 13, 3:31 PM CT`: weekday and date)",
    "lifeos/platform/scheduled_jobs.py": "the scheduled-jobs table in a report, with its own date",
    "lifeos/dcc/header.py": "the page's date line: `Friday, Nov 13 · Updated 3:31 PM CT`, weekday and date",
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
                self.assertEqual(len(re.findall(r"\d{1,2}:\d{2} [AP]M", heading)), 1, "the time is shown once: " + heading)         # never a second time (the old `mail: ... 10:24 AM CT` clause)
                self.assertNotIn("mail:", heading)
                self.assertNotRegex(heading, r"\b(UTC|CST|CDT|GMT)\b")                                                          # always "CT", never a zone name or UTC

    def test_every_router_region_has_a_rule_so_a_new_card_cannot_skip_the_stamp(self):
        covered = set(HEADINGS) | set(EXEMPT)
        self.assertEqual(sorted(set(router.OWNERS) - covered), [], "a region has no heading rule: add it to HEADINGS (or EXEMPT, with the reason) in this test")

    def test_the_degraded_stale_and_unread_forms_keep_the_title_and_the_date_and_clock_in_the_heading(self):
        self.assertEqual(outreach_card.heading(NOW, "success", "14-day scan (Outlook personal unread)"), "🤝 Recruiter Outreach (Updated Nov 12 · 1:35 PM CT, Outlook personal unread)")
        self.assertEqual(outreach_card.heading(NOW, "success", "new mail"), "🤝 Recruiter Outreach (Updated Nov 12 · 1:35 PM CT)")
        self.assertEqual(outreach_card.heading(NOW, "skipped"), "🤝 Recruiter Outreach (Redrawn Nov 12 · 1:35 PM CT)")
        self.assertEqual(jira.heading_text(NOW, NOW), "JIRA Execution (STALE · last accepted Nov 12 · 1:35 PM CT)")
        self.assertEqual(amazon.heading_text(NOW, True), "Amazon Orders (DEGRADED at Nov 12 · 1:35 PM CT)")
        self.assertEqual(physmail.heading_text(NOW, True), "Mail Alerts (DEGRADED at Nov 12 · 1:35 PM CT)")
        self.assertEqual(bills.heading_text({"taken_at": NOW.isoformat()}, True), "Bills: This Week (STALE · last accepted Nov 12 · 1:35 PM CT)")
        self.assertEqual(agenda._heading_text({"taken_at": NOW.isoformat()}, True), "Calendar (STALE · last accepted Nov 12 · 1:35 PM CT)")
        self.assertTrue(megibow.status(NOW, degraded=True).startswith("MegIBOW (DEGRADED at Nov 12 · 1:35 PM CT)"))
        self.assertTrue(capacity_card.render({"pools": [], "unavailable": [], "safety_cap": None}, NOW)[0].startswith("AI Capacity (Updated Nov 12 · 1:35 PM CT) · "))
        self.assertEqual(oracle_heading().split(" (")[1][:len("Updated Nov 12 · 1:35 PM CT")], "Updated Nov 12 · 1:35 PM CT")

    def test_one_formatter_builds_the_stamp_for_every_form_and_a_new_year_and_zone_are_right(self):
        self.assertEqual(ct_stamp.when(dt.datetime(2026, 11, 13, 21, 31, tzinfo=dt.timezone.utc)), "Nov 13 · 3:31 PM CT")            # UTC in, Central out
        self.assertEqual(ct_stamp.when(dt.datetime(2026, 7, 4, 17, 5, tzinfo=dt.timezone.utc)), "Jul 4 · 12:05 PM CT")             # CDT, no leading zero on the day or the hour
        self.assertEqual(ct_stamp.when(dt.datetime(2026, 11, 13, 0, 5)), "Nov 13 · 12:05 AM CT")                                    # a naive moment is already Central; midnight reads 12 AM
        self.assertEqual(ct_stamp.when(dt.datetime(2026, 11, 13, 12, 0, tzinfo=ZoneInfo("America/Chicago"))), "Nov 13 · 12:00 PM CT")
        self.assertEqual(ct_stamp.updated(NOW), "Updated Nov 12 · 1:35 PM CT")

    def test_no_card_builds_a_stamp_without_the_shared_formatter(self):
        """A line that writes `Updated {`, `DEGRADED at {`, `last accepted {` or `Redrawn {` must take its moment from ct_stamp; a line that formats a clock and then writes ` CT` by hand is a hand-built stamp. Allow-list: not a Daily Report card heading."""
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
        self.assertEqual(offenders, [], "build the stamp with lifeos.platform.ct_stamp.when / updated")

    def test_the_hand_built_stamp_this_test_hunts_is_caught(self):
        for wrong in ('return f"{TITLE} (Updated {now.strftime(\'%-I:%M %p\')} CT)"', 'f"DEGRADED at {clock}"', 'x = f"last accepted {taken}"'):
            self.assertTrue(re.search(r"(?:Updated|DEGRADED at|last accepted|Redrawn) ?\{|%-?I:%M %p[^\n]*\bCT[\"'\)\}]", wrong), wrong)

    def test_the_negative_controls_the_old_and_wrong_forms_are_caught(self):
        form = heading_form("Mail Alerts")
        self.assertRegex("Mail Alerts (Updated Nov 12 · 1:35 PM CT)", form)
        for wrong in ("Mail Alerts (Updated 1:35 PM CT)", "Mail Alerts (Updated Nov 12 1:35 PM CT)", "Mail Alerts (Updated Nov 12, 1:35 PM CT)", "Mail Alerts (Updated Nov 12 · 1:35 PM CT, mail: new mail 1:35 PM CT)", "Mail Alerts", "Updated 1:35 PM CT · 11 waiting", "Mail Alerts (Updated 1:35 PM CT, mail: new mail 1:35 PM CT)", "Mail Alerts · Updated 1:35 PM CT",
                      "Mail Alerts (Updated 1:35 PM CST)", "Mail Alerts (Updated 1:35 PM UTC)", "Mail Alerts (Last refreshed 1:35 PM CT)"):
            self.assertNotRegex(wrong, form, wrong)


if __name__ == "__main__":
    unittest.main()
