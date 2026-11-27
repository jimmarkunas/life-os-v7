"""the sign-in renewal reminder: one styled inbox item per profile per expiry, three days ahead or once lapsed."""

import unittest
from datetime import datetime, timezone

from lifeos.platform import agent_inbox
from lifeos.sources.cards import oracle_card, signin_reminder

NOW = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)
METER, FIT = "2026-11-29T04:35:00+00:00", "2026-11-29T19:01:00+00:00"  # Sat 10:35 PM CT, Sun 1:01 PM CT


class Plan(unittest.TestCase):
    def test_both_current_sign_ins_are_inside_three_days_and_get_one_item_each(self):
        items = signin_reminder.plan(
            {"meter": {"state": "OK", "expires": METER}, "fit": {"state": "OK", "expires": FIT}}, NOW
        )
        self.assertEqual([i["key"] for i in items], ["signin-meter-202611282235", "signin-fit-202611291301"])
        self.assertIn("Nov 28 2026, 10:35 PM CT", items[0]["subject"])
        self.assertIn("**start usage meter renewal**", items[0]["details"])
        self.assertIn("**cloud fit renewed**", items[1]["details"])

    def test_the_item_normalizes_and_renders_as_a_callout_then_a_table(self):
        item = signin_reminder.plan({"meter": {"state": "OK", "expires": METER}}, NOW)[0]
        normal = agent_inbox.normalize(item, NOW)
        self.assertEqual(normal["key"], item["key"])
        kinds = [b["type"] for b in agent_inbox.body_blocks(item["details"])]
        self.assertEqual(kinds[:2], ["callout", "table"])

    def test_a_sign_in_more_than_three_days_out_gets_nothing(self):
        self.assertEqual(
            signin_reminder.plan({"meter": {"state": "OK", "expires": "2026-12-03T00:00:00+00:00"}}, NOW), []
        )

    def test_a_renewed_sign_in_is_a_new_key_and_a_repeat_is_the_same_key(self):
        a = signin_reminder.plan({"meter": {"state": "OK", "expires": METER}}, NOW)[0]["key"]
        self.assertEqual(a, signin_reminder.plan({"meter": {"state": "OK", "expires": METER}}, NOW)[0]["key"])
        later = datetime(2026, 12, 3, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(
            signin_reminder.plan({"meter": {"state": "OK", "expires": "2026-12-05T04:35:00+00:00"}}, later)[0]["key"],
            "signin-meter-202612042235",
        )

    def test_a_lapsed_sign_in_is_now_urgent(self):
        item = signin_reminder.plan({"fit": {"state": "EXPIRED", "expires": "2026-11-25T19:01:00+00:00"}}, NOW)[0]
        self.assertEqual(item["urgency"], "Now")
        self.assertIn("has lapsed", item["subject"])

    def test_missing_or_unreadable_readings_give_nothing(self):
        self.assertEqual(signin_reminder.plan(None, NOW), [])
        self.assertEqual(
            signin_reminder.plan(
                {"meter": {"state": "OK", "expires": "garbage"}, "fit": {"state": "OK", "expires": None}}, NOW
            ),
            [],
        )


class Send(unittest.TestCase):
    def test_counts_sent_and_failed_and_never_raises(self):
        def deliver(item):
            if item["key"].startswith("signin-fit"):
                raise RuntimeError("boom")
            return {"ok": True, "created": 1}

        items = signin_reminder.plan(
            {"meter": {"state": "OK", "expires": METER}, "fit": {"state": "OK", "expires": FIT}}, NOW
        )
        self.assertEqual(signin_reminder.send_all(items, {}, deliver), {"sent": 1, "failed": 1})

    def test_the_card_helper_returns_a_count_and_swallows_failures(self):
        reading = {"signins": {"meter": {"state": "OK", "expires": METER}}}
        self.assertEqual(oracle_card.reminders(reading, {}, NOW, lambda item: {"ok": True}), 1)
        self.assertEqual(oracle_card.reminders(reading, {}, NOW, lambda item: 1 / 0), 0)
        self.assertEqual(oracle_card.reminders(None, {}, NOW, None), 0)

    def test_without_a_send_the_card_writer_client_delivers_through_the_inbox(self):
        from unittest import mock

        reading = {"signins": {"meter": {"state": "OK", "expires": METER}}}
        with mock.patch("lifeos.sources.cards.oracle_card.inbox_send.deliver", return_value={"ok": True}) as deliver:
            self.assertEqual(oracle_card.reminders(reading, {}, NOW, None, "writer"), 1)
        self.assertEqual(deliver.call_args.args[1], "writer")


if __name__ == "__main__":
    unittest.main()
