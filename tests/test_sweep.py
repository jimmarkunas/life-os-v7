import unittest

from pipeline import config
from pipeline.sweep import sweep


class FakeGmail:
    def __init__(self, senders):
        self.senders, self.calls = senders, []

    def label_id(self, name, create=False):
        return "LBL"

    def list_ids(self, query, limit=5000):
        return list(self.senders)

    def sender(self, message_id):
        return self.senders[message_id]

    def relabel(self, ids, add=(), remove=()):
        self.calls.append((list(ids), list(add), list(remove)))


SENDERS = {
    "1": "Lensa <jobalert@lensa.com>", "2": "noreply@jobright.ai",
    "3": "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
    "4": "messages-noreply@linkedin.com",           # LinkedIn messages: not a job alert
    "5": "r5f-v0c-a5q@user.dice.com",               # Dice recruiter relay: not a newsletter
    "6": "friend@gmail.com", "7": "noreply@lensa.com.evil.example",
}


class SweepTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(config.classify("Lensa <aggregated@lensa.com>"), "lensa")
        self.assertIsNone(config.classify("noreply@lensa.com.evil.example"))
        self.assertIsNone(config.classify("invitations@linkedin.com"))

    def test_dry_run_changes_nothing(self):
        gmail = FakeGmail(SENDERS)
        result = sweep(gmail, live=False)
        self.assertEqual(gmail.calls, [])
        self.assertEqual((result["would_move"], result["moved"], result["skipped_not_newsletter"]), (3, 0, 4))

    def test_live_moves_only_allowlisted_and_leaves_inbox(self):
        gmail = FakeGmail(SENDERS)
        result = sweep(gmail, live=True)
        self.assertEqual(gmail.calls, [(["1", "2", "3"], ["LBL"], ["INBOX"])])
        self.assertEqual(result["by_rule"], {"jobright": 1, "lensa": 1, "linkedin-alerts": 1})


if __name__ == "__main__":
    unittest.main()
