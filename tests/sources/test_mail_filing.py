import unittest

from lifeos.sources import mail_filing as filing
from lifeos.platform.gmail import GmailError

VENDOR = filing.SENDER


class FakeGmail:
    def __init__(self, senders, fail=None, stuck=None):
        self.senders, self.fail, self.stuck, self.labels, self.calls, self.created = senders, fail, stuck, {m: ["INBOX", "UNREAD"] for m in senders}, [], []

    def list_ids(self, query, limit):
        self.calls.append(("list", query, limit))
        return list(self.senders)[:limit]

    def label_id(self, name, create=False):
        self.created.append((name, create))
        return "Label_pm"

    def sender(self, message_id):
        return self.senders[message_id]

    def file_read(self, message_id, label_id):
        if message_id == self.fail:
            raise GmailError("GMAIL_HTTP_500")
        self.calls.append(("file", message_id, label_id))
        if message_id != self.stuck:
            self.labels[message_id] = [label_id]

    def message_labels(self, message_id):
        return self.labels[message_id]


class Filing(unittest.TestCase):
    def test_a_dry_run_counts_and_changes_nothing(self):
        gmail = FakeGmail({"m1": f"Vendor <{VENDOR}>", "m2": VENDOR})
        self.assertEqual(filing.run(0, False, gmail=gmail), {"listed": 2, "filed": 0, "skipped_sender": 0, "failed": 0, "why": {}})
        self.assertEqual([c[0] for c in gmail.calls], ["list"])
        self.assertEqual(gmail.created, [])
        self.assertIn(f"from:{VENDOR}", gmail.calls[0][1])

    def test_live_marks_read_moves_and_reads_back(self):
        gmail = FakeGmail({"m1": VENDOR, "m2": f"Vendor <{VENDOR}>"})
        counts = filing.run(0, True, gmail=gmail)
        self.assertEqual((counts["listed"], counts["filed"], counts["failed"]), (2, 2, 0))
        self.assertEqual(gmail.created, [("Physical Mail", True)])
        self.assertEqual(gmail.labels, {"m1": ["Label_pm"], "m2": ["Label_pm"]})

    def test_only_the_exact_vendor_address_is_touched(self):
        other = VENDOR.replace("no-reply", "billing")
        gmail = FakeGmail({"m1": other, "m2": f"x <{other}>, {VENDOR}", "m3": VENDOR})
        counts = filing.run(0, True, gmail=gmail)
        self.assertEqual((counts["filed"], counts["skipped_sender"]), (1, 2))
        self.assertEqual(gmail.labels["m1"], ["INBOX", "UNREAD"])

    def test_failures_are_fixed_codes_and_do_not_stop_the_rest(self):
        gmail = FakeGmail({"m1": VENDOR, "m2": VENDOR, "m3": VENDOR}, fail="m1", stuck="m2")
        counts = filing.run(0, True, gmail=gmail)
        self.assertEqual((counts["filed"], counts["failed"]), (1, 2))
        self.assertEqual(counts["why"], {"PHYSMAIL_FILING_MOVE_FAILED": 1, "PHYSMAIL_FILING_READBACK_MISMATCH": 1})

    def test_the_run_is_bounded(self):
        gmail = FakeGmail({f"m{i}": VENDOR for i in range(300)})
        self.assertEqual(filing.run(0, False, gmail=gmail)["listed"], filing.DEFAULT_LIMIT)


if __name__ == "__main__":
    unittest.main()
