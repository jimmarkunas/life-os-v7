import unittest

from lifeos.sources.cards import mail_filing as filing
from lifeos.platform.gmail import GmailError
from tests.platform.test_isolate import main_exit

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


class OneBadMessageNeverFailsTheStage(unittest.TestCase):
    """LIFE-689: a message that cannot be filed is that message's failure; the others are filed and the stage exits 0. Only when every message fails, or the vendor's mail cannot be listed, does it fail."""

    def stage(self, gmail):
        return lambda limit, live: filing.run(limit, live, gmail=gmail)

    def exit_of(self, gmail):
        return main_exit(self.stage(gmail), "mail-filing", "PHYSMAIL_FILING_FAILED")

    def test_five_messages_with_one_failing_file_four_and_exit_zero(self):
        gmail = FakeGmail({f"m{i}": VENDOR for i in range(5)}, fail="m2")
        code, err, out = self.exit_of(gmail)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(sorted(m for m, labels in gmail.labels.items() if labels == ["Label_pm"]), ["m0", "m1", "m3", "m4"])
        self.assertIn('"item_codes": {"PHYSMAIL_FILING_MOVE_FAILED": 1}', out)
        self.assertIn('"why": {"PHYSMAIL_FILING_MOVE_FAILED": 1}', out)                    # the counts the stage always printed are unchanged

    def test_all_messages_failing_exit_one_with_the_fixed_code(self):
        gmail = FakeGmail({f"m{i}": VENDOR for i in range(5)})
        gmail.file_read = lambda message_id, label_id: (_ for _ in ()).throw(GmailError("GMAIL_HTTP_500"))
        code, err, _ = self.exit_of(gmail)
        self.assertEqual((code, err.strip()), (1, "MAIL-FILING FAILED: PHYSMAIL_FILING_FAILED"))

    def test_a_readback_mismatch_and_a_move_failure_keep_their_own_codes(self):
        counts = filing.run(0, True, gmail=FakeGmail({"m1": VENDOR, "m2": VENDOR, "m3": VENDOR}, fail="m1", stuck="m2"))
        self.assertEqual(counts["why"], {"PHYSMAIL_FILING_MOVE_FAILED": 1, "PHYSMAIL_FILING_READBACK_MISMATCH": 1})
        self.assertEqual((counts["items"], counts["item_failed"]), (3, 2))

    def test_an_error_that_is_not_a_gmail_error_no_longer_stops_the_run(self):
        gmail = FakeGmail({"m1": VENDOR, "m2": VENDOR})
        original = gmail.sender
        gmail.sender = lambda message_id: (_ for _ in ()).throw(ValueError("boom")) if message_id == "m1" else original(message_id)
        counts = filing.run(0, True, gmail=gmail)
        self.assertEqual((counts["filed"], counts["failed"], counts["why"]), (1, 1, {"PHYSMAIL_FILING_MOVE_FAILED": 1}))

    def test_when_the_vendors_mail_cannot_be_listed_the_stage_itself_fails(self):
        gmail = FakeGmail({"m1": VENDOR})
        gmail.list_ids = lambda query, limit: (_ for _ in ()).throw(GmailError("GMAIL_HTTP_500"))
        code, err, _ = self.exit_of(gmail)
        self.assertEqual((code, err.strip()), (1, "MAIL-FILING FAILED: PHYSMAIL_FILING_FAILED"))

    def test_when_every_message_is_filed_the_counts_carry_no_item_keys(self):
        counts = filing.run(0, True, gmail=FakeGmail({"m1": VENDOR, "m2": VENDOR}))
        self.assertEqual(counts, {"listed": 2, "filed": 2, "skipped_sender": 0, "failed": 0, "why": {}})


if __name__ == "__main__":
    unittest.main()
