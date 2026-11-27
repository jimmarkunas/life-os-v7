"""Gmail message and thread reads may run a few at a time."""

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
        time.sleep(0.02 if name in ("m0", "m1") else 0.005)
        with self.lock:
            self.now -= 1

    def mail_record(self, message_id):
        self._enter(message_id)
        if message_id in BAD:
            raise GmailError("GMAIL_MESSAGE_INCOMPLETE")
        return {
            "id": message_id,
            "received_at": "2026-11-17T10:00:00+00:00",
            "headers": {"from": f"Person <{message_id}@example.test>", "subject": f"Subject {message_id}"},
            "body_text": f"body {message_id}",
            "label_ids": ["INBOX"],
            "thread": "t" + message_id,
        }

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
        self.assertEqual(parallel, sequential)  # records, order and unreadable count
        self.assertEqual((len(sequential[0]), sequential[1]), (len(IDS) - len(BAD), len(BAD)))
        self.assertEqual([r["id"] for r in parallel[0]], [i for i in IDS if i not in BAD])
        self.assertEqual(one.max, 1)  # the default is the old one-at-a-time read
        self.assertTrue(1 < many.max <= 4, many.max)  # side by side, never more than the cap
        self.assertTrue(many.first_alone)  # the first read goes alone (one access-token fetch)
        self.assertEqual(sorted(many.order), sorted(IDS))  # every message read exactly once

    def test_sent_mail_reads_each_thread_once_with_the_same_results_and_failure_flags(self):
        threads = ["1" * 8 + c for c in "abcdef"]
        evidence = [
            (ParentQuery("Co" + t[-1], "Role"), RoundQuery(True, source=f"https://mail.google.com/mail/u/0/#all/{t}"))
            for t in threads
        ]
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
        self.assertEqual(sorted(many.order), sorted(threads))  # once each
        self.assertTrue(got[1])
        self.assertTrue(1 < many.max <= 4, many.max)
        self.assertTrue(many.first_alone)


if __name__ == "__main__":
    unittest.main()


class SourcesOneByOne(unittest.TestCase):
    """the sources are read one after another (the side-by-side read of PR 1318 is withdrawn)"""

    def test_records_order_and_count_and_the_fixed_second_names(self):
        from unittest import mock

        def outlook(account, label, now, limit=None, days=None):
            if label == "bad":
                raise mail.MailError("MAIL_OUTLOOK_LISTING_INCOMPLETE")
            return [{"id": label}], 0

        def gmail_records(gm, now, limit=None, days=None, workers=1, timing=None, heal=False):
            if timing is not None:
                timing.update(listed=12, list_s=1.5, read=12, read_s=3.0)
            return [{"id": "g"}], 1

        seconds, detail = {}, {}
        with (
            mock.patch.object(mail, "outlook_records", outlook),
            mock.patch.object(mail, "gmail_records", gmail_records),
        ):
            got = interview_evidence.read_mail(
                {},
                NOW,
                gmail=object(),
                outlook_accounts=[("one", object()), ("bad", object())],
                seconds=seconds,
                detail=detail,
            )
        self.assertEqual(got, ([{"id": "g"}, {"id": "one"}], 2))  # Gmail first, then the accounts in order
        self.assertEqual(set(seconds), {"gmail", "outlook_1", "outlook_2"})  # fixed names, never a label
        self.assertTrue(all(isinstance(v, float) and v >= 0 for v in seconds.values()))
        self.assertEqual(detail["gmail"], {"listed": 12, "list_s": 1.5, "read": 12, "read_s": 3.0})

    def test_gmail_records_reports_listed_and_read_counts_with_seconds(self):
        timing = {}
        records, bad = mail.gmail_records(FakeGmail(), NOW, workers=4, timing=timing)
        self.assertEqual((len(records), bad), (len(IDS) - len(BAD), len(BAD)))
        self.assertEqual((timing["listed"], timing["read"]), (len(IDS), len(IDS)))
        self.assertTrue(timing["list_s"] >= 0 and timing["read_s"] >= 0)
        self.assertEqual(mail.gmail_records(FakeGmail(), NOW)[0], records)  # the timing changes nothing it returns


class HealingGmail(FakeGmail):
    """m3 is gone since the listing (404), m5 fails once then reads, m8 never parses, m10 is rate limited every time."""

    def __init__(self):
        super().__init__()
        self.calls, self.retries = {}, 7

    def mail_record(self, message_id):
        self.calls[message_id] = self.calls.get(message_id, 0) + 1
        if message_id == "m3":
            raise GmailError("GET https://gmail.googleapis.com/gmail/v1/users/me/messages -> HTTP 404 notFound")
        if message_id == "m5" and self.calls[message_id] == 1:
            raise GmailError("GET https://gmail.googleapis.com/gmail/v1/users/me/messages -> HTTP 500 backendError")
        if message_id == "m8":
            raise GmailError("GMAIL_MESSAGE_INCOMPLETE")
        if message_id == "m10":
            raise GmailError(
                "GET https://gmail.googleapis.com/gmail/v1/users/me/messages -> HTTP 429 rateLimitExceeded"
            )
        return {
            "id": message_id,
            "received_at": "2026-11-17T10:00:00+00:00",
            "headers": {"from": f"P <{message_id}@example.test>", "subject": "s"},
            "body_text": "b",
            "label_ids": ["INBOX"],
            "thread": "t",
        }


class HealingReads(unittest.TestCase):
    def test_the_default_read_is_unchanged_and_the_healing_read_tries_again_and_drops_the_deleted(self):
        plain = mail.gmail_records(HealingGmail(), NOW)
        self.assertEqual((len(plain[0]), plain[1]), (12 - 4, 4))  # the old behaviour: every failed read is unreadable
        timing = {}
        gm = HealingGmail()
        records, unreadable = mail.gmail_records(gm, NOW, workers=4, timing=timing, heal=True)
        ids = [r["id"] for r in records]
        self.assertIn("m5", ids)  # the one more try read it
        self.assertNotIn("m3", ids)
        self.assertEqual(unreadable, 2)  # m8 and m10 stay unreadable
        self.assertEqual(timing["gone"], 1)
        self.assertEqual(
            timing["unreadable"], {"m8": "GMAIL_MESSAGE_INCOMPLETE", "m10": "HTTP_429_RATELIMITEXCEEDED"}
        )  # fixed codes, never text
        self.assertEqual(timing["retries"], 7)
        self.assertEqual(gm.calls["m5"], 2)  # tried once more, no more
        self.assertEqual([i for i in ids], [i for i in IDS if i not in ("m3", "m8", "m10")])  # order kept


class InterviewMailMessages(unittest.TestCase):
    """a Gmail message that stays unreadable follows the same streak as an unreadable sent thread, and a message that can
    never parse is permanent at once.
    """

    def run_stage(self, unreadable, before):
        from unittest import mock
        from tests.sources.support import LIVE, JourneyFixture
        from lifeos.interview import create

        class Case(JourneyFixture, unittest.TestCase):
            def runTest(self):
                pass

        case = Case()
        case.setUp()

        def fake_read(environ, now, gmail=None, outlook_accounts=None, days=14, seconds=None, detail=None):
            detail["gmail"] = {"listed": 3, "unreadable": dict(unreadable)}
            return [], 0

        calendar = type(
            "Calendar", (), {"list_events": lambda s, a, b: [], "request": lambda s, m, p, params=None: {"items": []}}
        )()
        with (
            mock.patch.object(interview_evidence, "read_mail", fake_read),
            mock.patch.object(interview_evidence.pipeline, "read_applied", return_value=[]),
            mock.patch.object(interview_evidence.streaks, "previous", return_value=before),
            mock.patch.object(interview_evidence.streaks, "save", return_value=True),
        ):
            return interview_evidence.run(
                20,
                True,
                environ=LIVE,
                client=case.notion,
                calendar=calendar,
                now=NOW,
                ledger=object(),
                records=None,
                ledger_rows=case.ledger.for_company,
                writer=case.ledger,
                recruiters=case.recruiters,
            )

    def test_a_message_gmail_cannot_parse_is_permanent_at_once_and_a_transient_one_fails_then_turns_permanent(self):
        permanent = self.run_stage({"m8": "GMAIL_MESSAGE_INCOMPLETE"}, {})
        self.assertEqual(
            (permanent["failed"], permanent["permanent"].get("mail_message_unreadable"), permanent["mail_causes"]),
            (0, 1, {"gmail_message_incomplete": 1}),
        )
        self.assertNotIn("mail_unavailable", permanent["why"])
        first = self.run_stage({"m10": "HTTP_429_RATELIMITEXCEEDED"}, {})
        self.assertGreater(first["failed"], 0)
        self.assertNotIn("mail_message_unreadable", first["permanent"])
        label = interview_evidence.streaks.label("message:m10")
        later = self.run_stage({"m10": "HTTP_429_RATELIMITEXCEEDED"}, {label: interview_evidence.streaks.FAILED_RUNS})
        self.assertEqual((later["failed"], later["permanent"].get("mail_message_unreadable")), (0, 1))
