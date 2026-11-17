"""LIFE-793: Gmail message and thread reads may run a few at a time. Outputs, order and counts must equal the one-by-one reads; the number at once is bounded; the first read goes alone."""
import threading
import time
import unittest
from datetime import datetime, timezone

from lifeos.platform import mail
from lifeos.platform.gmail import GmailError
from lifeos.sources.interview import interview_evidence
from lifeos.interview.models import ParentQuery, RoundQuery

NOW = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)
IDS = [f"m{n}" for n in range(12)]
BAD = {"m4", "m9"}


class FakeGmail:
    def __init__(self):
        self.lock, self.now, self.max, self.order, self.first_alone = threading.Lock(), 0, 0, [], None

    def list_ids_complete(self, query, limit, include_spam=False):
        return list(IDS)

    def _enter(self, name):
        with self.lock:
            if not self.order:
                self.first_alone = self.now == 0
            self.order.append(name)
            self.now += 1
            self.max = max(self.max, self.now)
        time.sleep(0.02 if name in ("m0", "m1") else 0.005)            # the early ids are slower: a pool that returned in finish order would reorder them
        with self.lock:
            self.now -= 1

    def mail_record(self, message_id):
        self._enter(message_id)
        if message_id in BAD:
            raise GmailError("GMAIL_MESSAGE_INCOMPLETE")
        return {"id": message_id, "received_at": "2026-11-17T10:00:00+00:00", "headers": {"from": f"Person <{message_id}@example.test>", "subject": f"Subject {message_id}"},
                "body_text": f"body {message_id}", "label_ids": ["INBOX"], "thread": "t" + message_id}

    def thread_sent(self, thread):
        self._enter(thread)
        if thread in {"1" * 8 + "b", "1" * 8 + "d"}:
            raise GmailError("GMAIL_THREAD_INCOMPLETE")
        return [(datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc), "Subject " + thread)]


class ParallelReads(unittest.TestCase):
    def test_parallel_records_equal_the_one_by_one_records_in_order_with_the_same_unreadable_count(self):
        one, many = FakeGmail(), FakeGmail()
        sequential = mail.gmail_records(one, NOW)
        parallel = mail.gmail_records(many, NOW, workers=4)
        self.assertEqual(parallel, sequential)                                                      # records, order and unreadable count
        self.assertEqual((len(sequential[0]), sequential[1]), (len(IDS) - len(BAD), len(BAD)))
        self.assertEqual([r["id"] for r in parallel[0]], [i for i in IDS if i not in BAD])
        self.assertEqual(one.max, 1)                                                                # the default is the old one-at-a-time read
        self.assertTrue(1 < many.max <= 4, many.max)                                                # side by side, never more than the cap
        self.assertTrue(many.first_alone)                                                           # the first read goes alone (one access-token fetch)
        self.assertEqual(sorted(many.order), sorted(IDS))                                           # every message read exactly once

    def test_sent_mail_reads_each_thread_once_with_the_same_results_and_failure_flags(self):
        threads = ["1" * 8 + c for c in "abcdef"]
        evidence = [(ParentQuery("Co" + t[-1], "Role"), RoundQuery(True, source=f"https://mail.google.com/mail/u/0/#all/{t}")) for t in threads]
        one_report, many_report = {}, {}
        old = interview_evidence.MAIL_WORKERS
        try:
            interview_evidence.MAIL_WORKERS = 1
            one = FakeGmail()
            expected = interview_evidence.sent_mail({}, evidence, one, one_report)
            interview_evidence.MAIL_WORKERS = 4
            many = FakeGmail()
            got = interview_evidence.sent_mail({}, evidence, many, many_report)
        finally:
            interview_evidence.MAIL_WORKERS = old
        self.assertEqual(got, expected)
        self.assertEqual(many_report, one_report)
        self.assertEqual(sorted(many.order), sorted(threads))                                       # once each
        self.assertTrue(got[1])                                                                     # the two unreadable threads still mark the run failed
        self.assertTrue(1 < many.max <= 4, many.max)
        self.assertTrue(many.first_alone)


if __name__ == "__main__":
    unittest.main()


class SourcesOneByOne(unittest.TestCase):
    """LIFE-793: the sources are read one after another (the side-by-side read of PR 1318 is withdrawn); each source reports its seconds, and Gmail reports its listed and read counts with seconds."""
    def test_records_order_and_count_and_the_fixed_second_names(self):
        from unittest import mock

        def outlook(account, label, now, limit=None, days=None):
            if label == "bad":
                raise mail.MailError("MAIL_OUTLOOK_LISTING_INCOMPLETE")
            return [{"id": label}], 0

        def gmail_records(gm, now, limit=None, days=None, workers=1, timing=None):
            if timing is not None:
                timing.update(listed=12, list_s=1.5, read=12, read_s=3.0)
            return [{"id": "g"}], 1
        seconds, detail = {}, {}
        with mock.patch.object(mail, "outlook_records", outlook), mock.patch.object(mail, "gmail_records", gmail_records):
            got = interview_evidence.read_mail({}, NOW, gmail=object(), outlook_accounts=[("one", object()), ("bad", object())], seconds=seconds, detail=detail)
        self.assertEqual(got, ([{"id": "g"}, {"id": "one"}], 2))                                    # Gmail first, then the accounts in order; the failing one adds no record and counts once
        self.assertEqual(set(seconds), {"gmail", "outlook_1", "outlook_2"})                         # fixed names, never a label
        self.assertTrue(all(isinstance(v, float) and v >= 0 for v in seconds.values()))
        self.assertEqual(detail["gmail"], {"listed": 12, "list_s": 1.5, "read": 12, "read_s": 3.0})

    def test_gmail_records_reports_listed_and_read_counts_with_seconds(self):
        timing = {}
        records, bad = mail.gmail_records(FakeGmail(), NOW, workers=4, timing=timing)
        self.assertEqual((len(records), bad), (len(IDS) - len(BAD), len(BAD)))
        self.assertEqual((timing["listed"], timing["read"]), (len(IDS), len(IDS)))
        self.assertTrue(timing["list_s"] >= 0 and timing["read_s"] >= 0)
        self.assertEqual(mail.gmail_records(FakeGmail(), NOW)[0], records)                          # the timing changes nothing it returns
