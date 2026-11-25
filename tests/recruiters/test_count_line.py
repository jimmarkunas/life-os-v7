"""LIFE-973, synthetic only: the Recruiter Outreach callout's gray count line is the only text V7 writes there; the title, the link and the live view inside are never touched."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.recruiters import notion as store, outreach_card as card
from tests.recruiters.fakes import FakeRecruiters, page

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=ZoneInfo("America/Chicago"))
BLOCK = "4233c5a059268087b278cf739e1159f7"
LINK = {"type": "text", "plain_text": "All recruiter records ↗", "text": {"content": "All recruiter records ↗", "link": {"url": "https://example.com/all"}}, "annotations": {}}
TITLE = {"type": "text", "plain_text": "Recruiter Outreach\n", "text": {"content": "Recruiter Outreach\n"}, "annotations": {"bold": True}}
GRAY = lambda t: {"type": "text", "plain_text": t, "text": {"content": t}, "annotations": {"color": "gray"}}      # noqa: E731


class Client:
    def __init__(self, parts, kind="callout"):
        self.block, self.calls = {"id": BLOCK, "type": kind, kind: {"rich_text": parts}}, []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET":
            return self.block
        self.block[self.block["type"]]["rich_text"] = [{**p, "plain_text": p["text"]["content"]} for p in body["callout"]["rich_text"]]
        return {}


ENV = {"RECRUITER_COUNT_BLOCK_ID": BLOCK, "RECRUITER_OUTREACH": "on"}


class CountLine(unittest.TestCase):
    def test_only_the_gray_text_before_the_link_is_replaced(self):
        parts = [TITLE, GRAY("Count fills in on the next run · "), LINK]
        new = card.retext(parts, "3 need action · 1 overdue")
        self.assertEqual([p["text"]["content"] for p in new], ["Recruiter Outreach\n", "3 need action · 1 overdue · ", "All recruiter records ↗"])
        self.assertEqual((new[0], new[2]), (TITLE, LINK))
        self.assertEqual(new[1]["annotations"], {"color": "gray"})

    def test_a_callout_with_no_gray_text_gets_one_before_the_link(self):
        new = card.retext([TITLE, LINK], "0 need action · 0 overdue")
        self.assertEqual([p["text"]["content"] for p in new], ["Recruiter Outreach\n", "0 need action · 0 overdue · ", "All recruiter records ↗"])
        self.assertIsNone(card.retext([TITLE], "x"))

    def test_a_dry_run_writes_nothing_and_a_live_run_writes_one_patch_and_reads_it_back(self):
        client = Client([TITLE, GRAY("Count fills in on the next run · "), LINK])
        rows = FakeRecruiters([page("A B", action="Needs Action", follow_up="2026-11-20"), page("C D", action="Needs Action", follow_up="2026-11-26"), page("E F", action="Waiting")])
        counts = card.run(0, False, environ=ENV, notion=rows, client=client, now=NOW)
        self.assertEqual((counts["needs_action"], counts["overdue"], counts["would_change"], counts["verified"]), (2, 1, 1, False))
        self.assertEqual([c[0] for c in client.calls], ["GET"])
        counts = card.run(0, True, environ=ENV, notion=rows, client=client, now=NOW)
        self.assertEqual((counts["verified"], [c[0] for c in client.calls].count("PATCH")), (True, 1))
        self.assertIn("2 need action · 1 overdue", "".join(p["plain_text"] for p in client.block["callout"]["rich_text"]))
        card.run(0, True, environ=ENV, notion=rows, client=client, now=NOW)
        self.assertEqual([c[0] for c in client.calls].count("PATCH"), 1)                       # the same line is not written twice

    def test_closed_done_and_inactive_rows_are_not_counted(self):
        rows = FakeRecruiters([page("A B", action="Needs Action"), page("C D", action="Needs Action", done=True), page("E F", action="Needs Action", active=False)])
        counts = card.run(0, False, environ=ENV, notion=rows, client=Client([TITLE, GRAY("x · "), LINK]), now=NOW)
        self.assertEqual(counts["needs_action"], 1)

    def test_an_unconfigured_wrong_or_degraded_run_changes_nothing(self):
        rows = FakeRecruiters([page("A B", action="Needs Action")])
        for env, kind, code in (({}, "callout", "NOT_CONFIGURED"), ({"RECRUITER_COUNT_BLOCK_ID": BLOCK}, "paragraph", "ANCHOR_INVALID"), ({"RECRUITER_COUNT_BLOCK_ID": "abc"}, "callout", "ID_INVALID")):
            client = Client([TITLE, GRAY("x · "), LINK], kind)
            with self.assertRaises(card.OutreachError) as caught:
                card.run(0, True, environ={**ENV, **env} if env else {"RECRUITER_OUTREACH": "on"}, notion=rows, client=client, now=NOW)
            self.assertIn(code, str(caught.exception))
            self.assertNotIn("PATCH", [c[0] for c in client.calls])
        client = Client([TITLE, GRAY("x · "), LINK])
        with self.assertRaises(card.OutreachError) as caught:
            card.run(0, True, environ={**ENV, "RECRUITERS_STAGE_OUTCOME": "failure"}, notion=rows, client=client, now=NOW)
        self.assertIn("DEGRADED", str(caught.exception))
        self.assertEqual(client.calls, [])

    def test_without_the_switch_a_live_run_is_a_dry_run(self):
        client = Client([TITLE, GRAY("old · "), LINK])
        card.run(0, True, environ={"RECRUITER_COUNT_BLOCK_ID": BLOCK}, notion=FakeRecruiters([]), client=client, now=NOW)
        self.assertNotIn("PATCH", [c[0] for c in client.calls])


if __name__ == "__main__":
    unittest.main()
