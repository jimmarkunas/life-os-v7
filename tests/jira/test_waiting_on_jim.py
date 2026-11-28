"""The daily Decisions for Jim Agent Inbox item: what it lists, its format, once a day, and what it never touches."""

import unittest
from datetime import datetime, timezone

from lifeos.jira import waiting_on_jim as decisions


def adf(*items, heading="Jim must do first"):
    box = lambda text, done: {
        "type": "taskItem",
        "attrs": {"state": "DONE" if done else "TODO"},
        "content": [{"type": "text", "text": text}],
    }
    return {
        "type": "doc",
        "content": [
            {"type": "heading", "content": [{"type": "text", "text": heading}]},
            {"type": "taskList", "content": [box(text, done) for text, done in items]},
        ],
    }


def ticket(key, summary, status="To Do", labels=(), description=None):
    return {
        "key": key,
        "fields": {"summary": summary, "status": {"name": status}, "labels": list(labels), "description": description},
    }


NOW = datetime(2026, 11, 28, 15, 30, tzinfo=timezone.utc)  # 9:30 AM CT
ISSUES = [
    ticket(
        "LIFE-1",
        "Approve the Todoist grant",
        labels=["needs-jim"],
        description=adf(("Open Bitwarden and add the key", False)),
    ),
    ticket("LIFE-2", "Already answered", labels=["needs-jim"], description=adf(("Reply yes", True))),
    ticket("LIFE-3", "Check the dashboard card", status="Human UAT"),
    ticket("LIFE-4", "Waiting on a vendor", status="Blocked"),
    ticket("LIFE-5", "Plain build ticket", status="In Progress", labels=["other"]),
]


class Render(unittest.TestCase):
    def test_the_item_has_a_callout_a_table_and_an_already_decided_list_in_the_approved_format(self):
        subject, key, details = decisions.render(ISSUES, NOW)
        self.assertEqual((subject, key), ("Decisions for Jim Sat Nov 28", "decisions-2026-11-28"))
        lines = details.splitlines()
        self.assertEqual(lines[0], "3 decisions left, about 6 minutes in all. Reply with the bold phrase in your step.")
        self.assertEqual(lines[1], "| Ticket | What it is for | Your step | Where | Status |")
        self.assertEqual(
            lines[2],
            "| **LIFE-1** | Approve the Todoist grant | Open Bitwarden and add the key, then reply **release LIFE-1** | Bitwarden | ☐ |",
        )
        self.assertIn("**accept LIFE-3** or **reject LIFE-3**", lines[3])
        self.assertIn("**go LIFE-4**", lines[4])
        self.assertEqual(lines[5:], ["# Already decided (no reply needed)", "- ✅ LIFE-2: Already answered"])
        self.assertNotIn("LIFE-5", details)  # a ticket that is not waiting on Jim is not listed

    def test_nothing_waiting_says_so_in_one_line(self):
        self.assertEqual(decisions.render([ISSUES[4]], NOW)[2], "Nothing is waiting on you today.")

    def test_the_date_and_key_are_the_central_date_not_utc(self):
        late = datetime(2026, 11, 29, 3, 0, tzinfo=timezone.utc)  # 9:00 PM CT on the 28th
        self.assertEqual(decisions.render(ISSUES, late)[:2], ("Decisions for Jim Sat Nov 28", "decisions-2026-11-28"))

    def test_a_long_list_is_cut_to_25_rows_and_says_how_many_are_left(self):
        many = [ticket(f"LIFE-{n}", f"Item {n}", labels=["needs-jim"]) for n in range(100, 130)]
        details = decisions.render(many, NOW)[2]
        self.assertEqual(details.count("\n| **LIFE-"), 25)
        self.assertIn("5 more are open in Jira", details)
        self.assertLess(len(details), decisions.DETAILS_MAX)

    def test_secret_like_and_personal_strings_and_table_breaks_never_reach_the_item(self):
        odd = ticket("LIFE-9", "Mail me@example.com | token=abcdef0123456789abcdef", labels=["needs-jim"])
        details = decisions.render([odd], NOW)[2]
        self.assertNotIn("me@example.com", details)
        self.assertNotIn("abcdef0123456789", details)
        self.assertEqual(details.splitlines()[2].count("|"), 6)  # the pipe in the summary was replaced


class Run(unittest.TestCase):
    def fake(self, issues):
        class Jira:
            def get(self, path, params=None):
                return {"issues": issues, "isLast": True}

        return Jira()

    def run_with(self, now, live, issues=ISSUES, reply=None):
        sent = []

        class Client:  # the delivery is replaced below
            pass

        original = decisions.inbox_send.deliver

        def deliver(item, client, when, details_max):
            sent.append((item, details_max))
            return reply or {"ok": True, "created": 1}

        decisions.inbox_send.deliver = deliver
        try:
            return decisions.run(0, live, environ={}, jira=self.fake(issues), client=Client(), now=now), sent
        finally:
            decisions.inbox_send.deliver = original

    def test_before_6_am_central_nothing_is_written(self):
        out, sent = self.run_with(datetime(2026, 11, 28, 10, 0, tzinfo=timezone.utc), True)  # 4 AM CT
        self.assertEqual((out["written"], sent), ("before_6am", []))

    def test_a_dry_run_writes_nothing(self):
        out, sent = self.run_with(NOW, False)
        self.assertEqual((out["written"], out["tickets"], out["decided"], sent), ("dry_run", 4, 1, []))

    def test_a_live_run_sends_one_item_keyed_by_the_central_date_with_the_larger_details_limit(self):
        out, sent = self.run_with(NOW, True)
        self.assertEqual(out["written"], "created")
        ((item, limit),) = sent
        self.assertEqual(
            (item["from"], item["key"], item["urgency"], item["workspace"], limit),
            ("jira-decisions", "decisions-2026-11-28", "Today", "Personal", decisions.DETAILS_MAX),
        )
        again, _ = self.run_with(NOW, True, reply={"ok": True, "updated": 1})
        self.assertEqual(again["written"], "updated")

    def test_a_refused_delivery_is_a_fixed_code_not_a_silent_pass(self):
        with self.assertRaises(RuntimeError) as caught:
            self.run_with(NOW, True, reply={"ok": False, "reason": "inbox_not_found"})
        self.assertEqual(str(caught.exception), "DECISIONS_INBOX_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
