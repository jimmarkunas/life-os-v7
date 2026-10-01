import unittest

from lifeos.sources.newsletters import config
from lifeos.sources.newsletters.sweep import sweep


class FakeGmail:
    """Synthetic mailbox: each search query returns a fixed id list."""

    def __init__(self, results):
        self.results, self.calls = results, []

    def label_id(self, name, create=False):
        return "LBL"

    def list_ids(self, query, limit=5000):
        return list(self.results.get(query, []))

    def relabel(self, ids, add=(), remove=()):
        self.calls.append((list(ids), list(add), list(remove)))


RESULTS = {
    config.GMAIL_QUERIES["lensa"]: ["1", "2"],
    config.GMAIL_QUERIES["jobright"]: ["3", "2"],           # "2" also matched by lensa: counted once
    config.GMAIL_QUERIES["linkedin-alerts"]: ["4"],
}


class SweepTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(config.classify("Lensa <aggregated@lensa.com>"), "lensa")
        self.assertIsNone(config.classify("noreply@lensa.com.evil.example"))
        self.assertIsNone(config.classify("invitations@linkedin.com"))
        self.assertIsNone(config.classify("abc-123-xyz@user.dice.com"))   # synthetic recruiter relay

    def test_queries_never_include_recruiter_or_message_senders(self):
        text = " ".join(config.GMAIL_QUERIES.values())
        for forbidden in ("dice", "messages-noreply", "invitations", "inmail"):
            self.assertNotIn(forbidden, text)

    def test_dry_run_changes_nothing(self):
        gmail = FakeGmail(RESULTS)
        result = sweep(gmail, live=False)
        self.assertEqual(gmail.calls, [])
        self.assertEqual((result["would_move"], result["moved"]), (4, 0))

    def test_live_moves_each_message_once_and_leaves_inbox(self):
        gmail = FakeGmail(RESULTS)
        result = sweep(gmail, live=True)
        self.assertEqual(gmail.calls, [(["1", "2", "3", "4"], ["LBL"], ["INBOX"])])
        self.assertEqual(result["by_rule"], {"lensa": 2, "jobright": 1, "linkedin-alerts": 1})


if __name__ == "__main__":
    unittest.main()
