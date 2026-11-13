"""LIFE-609: every Daily Report card shows when it was last updated IN ITS HEADING, in Central Time: `<Title> (Updated H:MM AM CT)` (Jim, Nov 13 2026, after UAT: the stamp in the heading, the same
form as Calendar's; not a body line, not a second time, no `mail:` clause). Each card's own heading function is called with a fixed time and its output must match. A region added to the router
without a rule here fails; a card whose heading lacks the stamp fails; a stamp only in the body line fails. Names and times only."""
import datetime as dt
import json
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
from lifeos.platform import router
from lifeos.recruiters import outreach_card

NOW = dt.datetime(2026, 11, 12, 13, 35, tzinfo=ZoneInfo("America/Chicago"))
CLOCK = r"\d{1,2}:\d{2} [AP]M CT"
BODY_LINE = re.compile(r"Updated (?:\w{3} \w{3} \d{1,2}, |\w+, \w{3} \d{1,2} · |\w{3} \d{1,2} · )?" + CLOCK)          # the old, rejected place: a body line


def heading_form(title):
    """`<Title> (Updated H:MM AM CT)`; Oracle's stamp also carries its date, and a mail source that was not read may be named after the time (`, Outlook personal unread`)."""
    return re.compile(rf"^{re.escape(title)} \(Updated (?:[A-Z][a-z]{{2}} \d{{1,2}} · )?{CLOCK}(?:, [^()0-9:]+ unread)?\)$")


def oracle_heading():
    raw = {"schema": oracle.SCHEMA, "observed_at": "2026-11-12T19:34:30Z", "cost": {"amount": 0, "currency": "USD"}, "cpu_24h_pct": 5, "memory_pct": 50, "disk_pct": 40,
           "a1_cores": {"used": 2, "available": 0}, "reclaim": "NONE", "services": {key: True for key, _ in oracle.SERVICES}, "chatgpt": {"left_5h": 100, "left_week": 64},
           "backup": {"ok": True, "code": "ok", "age_h": 3}}
    return oracle.render(oracle.validate(json.dumps(raw), NOW), NOW)[1]


def hiring_heading():
    text = hiring_render.render({"rows": [], "reasons": []}, None, NOW)[0]
    return hiring_models.heading_text(hiring_models.split_status(text)[0])


def capacity_heading():
    return capacity_card.render({"pools": [], "unavailable": [], "safety_cap": None}, NOW)[0].split("\n")[0].split(" · ")[0]          # the callout's first line, up to its first " · " (the bold label)


# region (or card name) -> (the card's title, a function returning the heading the card writes for a fixed time)
HEADINGS = {
    router.JIRA_REGION: (router.JIRA_REGION, lambda: jira.heading_text(NOW)),
    router.CALENDAR_REGION: (router.CALENDAR_REGION, lambda: agenda._heading_text({"taken_at": NOW.isoformat()}, False)),
    router.HIRING_REGION: (router.HIRING_REGION, hiring_heading),
    router.COMMS_REGION: (router.COMMS_REGION, lambda: comms_card.render(comms_card.UNAVAILABLE, NOW)[1]),
    router.ORACLE_REGION: (router.ORACLE_REGION, oracle_heading),
    router.RECRUITER_OUTREACH_REGION: (router.RECRUITER_OUTREACH_REGION, lambda: outreach_card.heading("1:35 PM CT")),
    "MegIBOW": ("MegIBOW", lambda: megibow.status(NOW).split(" · ")[0]),
    "AI Capacity": ("AI Capacity", capacity_heading),
}
# Not a Daily Report card heading, with the reason:
EXEMPT = {
    router.ATTENTION_REGION: "LIFE-645: V7 retired the card that wrote it; the callout stays on the page, protected, and nothing stamps it",
    router.DCC_REGION: "the Command Center's stamp is the page's first line (lifeos/dcc/header.py, `Friday, Nov 13 · Updated 1:35 PM CT`), checked by the DCC tests",
    router.REPO_REGION: "the Weekly LIFE OS Review page, not the Daily Report (lifeos/platform/branches.py)",
}
# LIFE-609 rework, part 2: these three still write the stamp in their first body line. Their writer (report_region.replace_text) insists the heading is exactly the title, and
# that file is protected; the change that moves them is its own pull request. Empty this set when it lands.
PENDING = {router.BILLS_REGION: lambda: bills._status({"taken_at": NOW.isoformat()}, False),
           router.AMAZON_REGION: lambda: amazon.render([], NOW)[0][0]["paragraph"]["rich_text"][0]["text"]["content"],
           router.MAIL_ALERTS_REGION: None}


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
        covered = set(HEADINGS) | set(EXEMPT) | set(PENDING)
        self.assertEqual(sorted(set(router.OWNERS) - covered), [], "a region has no heading rule: add it to HEADINGS (or EXEMPT, with the reason) in this test")

    def test_the_pending_set_is_exactly_the_three_cards_waiting_on_the_protected_file(self):
        self.assertEqual(sorted(PENDING), sorted([router.BILLS_REGION, router.AMAZON_REGION, router.MAIL_ALERTS_REGION]))
        for region in (router.BILLS_REGION, router.AMAZON_REGION):
            self.assertRegex(PENDING[region](), BODY_LINE)                                                                      # until they move, the stamp is still on the page

    def test_the_degraded_stale_and_unread_forms_keep_the_title_and_the_clock_in_the_heading(self):
        self.assertEqual(outreach_card.heading("1:35 PM CT", "success", "14-day scan (Outlook personal unread)"), "🤝 Recruiter Outreach (Updated 1:35 PM CT, Outlook personal unread)")
        self.assertEqual(outreach_card.heading("1:35 PM CT", "success", "new mail"), "🤝 Recruiter Outreach (Updated 1:35 PM CT)")
        self.assertEqual(jira.heading_text(NOW, NOW), "JIRA Execution (STALE · last accepted Nov 12 1:35 PM CT)")
        self.assertTrue(megibow.status(NOW, degraded=True).startswith("MegIBOW (DEGRADED at 1:35 PM CT)"))

    def test_the_negative_controls_the_old_and_wrong_forms_are_caught(self):
        form = heading_form("Mail Alerts")
        self.assertRegex("Mail Alerts (Updated 1:35 PM CT)", form)
        for wrong in ("Mail Alerts", "Updated 1:35 PM CT · 11 waiting", "Mail Alerts (Updated 1:35 PM CT, mail: new mail 1:35 PM CT)", "Mail Alerts · Updated 1:35 PM CT",
                      "Mail Alerts (Updated 1:35 PM CST)", "Mail Alerts (Updated 1:35 PM UTC)", "Mail Alerts (Last refreshed 1:35 PM CT)"):
            self.assertNotRegex(wrong, form, wrong)


if __name__ == "__main__":
    unittest.main()
