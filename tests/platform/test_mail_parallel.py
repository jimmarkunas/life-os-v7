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
