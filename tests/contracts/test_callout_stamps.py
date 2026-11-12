"""LIFE-609: every card that writes a Daily Report callout shows when it was last updated, in Central Time ("Updated <h:mm AM/PM> CT"). A region added to the router without a rule here fails.

Eight cards have a small pure function for that line, so the real function is called with a fixed time and its output must match. The others (mail, iMessage comms, Oracle, Hiring, AI capacity,
Recruiter Outreach) build the line deep inside a larger render that needs a full fixture, so for them the module's code is checked for the stamp (a string that says "Updated" and a time ending
"CT"); their own card tests exercise the real output. Names and times only."""
import ast
import datetime as dt
import re
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from lifeos.agenda import card as agenda
from lifeos.amazon import card as amazon
from lifeos.attention import card as attention
from lifeos.bills import card as bills
from lifeos.dcc import header as dcc
from lifeos.jira import card as jira
from lifeos.megibow import render as megibow
from lifeos.platform import branches, router

ROOT = Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 11, 12, 13, 35, tzinfo=ZoneInfo("America/Chicago"))
STAMP = re.compile(r"Updated (?:\w{3} \w{3} \d{1,2}, |\w+, \w{3} \d{1,2} · |\w{3} \d{1,2} · )?\d{1,2}:\d{2} [AP]M CT")


def amazon_line():
    return amazon.render([], NOW)[0][0]["paragraph"]["rich_text"][0]["text"]["content"]


def branches_line():
    return branches.lines([], NOW)[0]


# region -> a function returning the callout's update line for a fixed time
CALLED = {
    router.JIRA_REGION: lambda: jira.heading_text(NOW),
    router.CALENDAR_REGION: lambda: agenda._status({"taken_at": NOW.isoformat()}, False),
    router.BILLS_REGION: lambda: bills._status({"taken_at": NOW.isoformat()}, False),
    router.AMAZON_REGION: amazon_line,
    router.ATTENTION_REGION: lambda: attention._status(2, NOW),
    router.DCC_REGION: lambda: dcc.text(NOW),
    router.REPO_REGION: branches_line,
    "MegIBOW": lambda: megibow.status(NOW),
}
# region -> the module whose code must carry the stamp (its own card tests exercise the real output)
STRUCTURAL = {
    router.MAIL_ALERTS_REGION: "lifeos/physmail/card.py",
    router.COMMS_REGION: "lifeos/imessage/comms_card.py",
    router.ORACLE_REGION: "lifeos/oraclecloud/card.py",
    router.HIRING_REGION: "lifeos/hiring/render.py",
    router.RECRUITER_OUTREACH_REGION: "lifeos/recruiters/outreach_card.py",
    "AI Capacity": "lifeos/jobs/fit/capacity_card.py",
}


def carries_the_stamp(path):
    """True when the module builds a string that says "Updated" and a time ending " CT" (a plain or an f-string)."""
    tree = ast.parse((ROOT / path).read_text())
    words, times = False, False
    for node in ast.walk(tree):
        pieces = [node.value] if isinstance(node, ast.Constant) and isinstance(node.value, str) else [p.value for p in node.values if isinstance(p, ast.Constant)] if isinstance(node, ast.JoinedStr) else []
        for text in pieces:
            words = words or "Updated" in text
            times = times or text.rstrip().endswith(" CT") or " CT" in text
    return words and times


class EveryCalloutShowsWhenItWasUpdated(unittest.TestCase):
    def test_each_pure_renderer_produces_an_updated_time_in_ct(self):
        for region, render in CALLED.items():
            with self.subTest(region=region):
                line = render()
                self.assertRegex(line, STAMP, line)
                self.assertNotRegex(line, r"\b(UTC|CST|CDT|GMT)\b")                              # always "CT", never a zone name or UTC

    def test_the_heavier_renderers_carry_the_stamp_in_their_code(self):
        for region, path in STRUCTURAL.items():
            with self.subTest(region=region):
                self.assertTrue(carries_the_stamp(path), f"{path} writes a callout without an 'Updated <time> CT' line")

    def test_every_router_region_has_a_rule_so_a_new_card_cannot_skip_the_stamp(self):
        covered = set(CALLED) | set(STRUCTURAL)
        self.assertEqual(sorted(set(router.OWNERS) - covered), [], "a region has no stamp rule: add it to CALLED or STRUCTURAL in this test")

    def test_the_negative_control_a_module_without_the_stamp_is_caught(self):
        self.assertFalse(carries_the_stamp("lifeos/platform/ct_stamp.py"))                       # it builds times but never says "Updated"
        self.assertNotRegex("Last refreshed Thu Nov 12, 1:35 PM CT", STAMP)
        self.assertNotRegex("Updated Thu Nov 12, 1:35 PM CST", STAMP)


if __name__ == "__main__":
    unittest.main()
