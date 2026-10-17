"""LIFE-64: the read-only live cause check (diag.yml job `trace`, stage newsletter-trace): each parsed card of ONE message classified against the Jobs store, over the real SQL."""
import ast
import hashlib
import json
import re
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from lifeos.jobs import identity, store
from lifeos.sources.newsletters import cardtrace, ingest
from tests.jobs.test_newsletter_golden_path import Mailbox, alert
from tests.kit.jobsdb import JobsDB

MID = "18f0a1b2c3d4e5f6"
REF = hashlib.sha256(MID.encode()).hexdigest()
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


class FailingGmail:
    message = None

    def list_ids(self, query, limit=5000):
        raise cardtrace.GmailError("boom with a secret subject")


class CardTraceTests(unittest.TestCase):
    def setUp(self):
        self.box = Mailbox({MID: ("Jobright <alerts@jobright.ai>", alert("v1", "v2", "v3", "v4", "v5", "v6"), time.time() - 60), "other-mail": ("Jobright <alerts@jobright.ai>", alert("v2"), time.time() - 60)})
        for labels in self.box.labels.values():
            labels.add("j-newsletters")
        self.db = JobsDB()
        self.addCleanup(self.db.raw.close)
        self.cards = ingest.PARSERS["jobright"](self.box.messages[MID][1])

    def job(self, card, status="NEW"):
        n = self.db.raw.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM v7_jobs").fetchone()[0]
        self.db.insert("v7_jobs", id=n, dedupe_key=identity.url_hash(card.url), status=status, title=card.title, company=card.company, source_url=card.url, first_seen=NOW, last_seen=NOW, updated_at=NOW)
        return n

    def source_row(self, job_id, message, hash_):
        self.db.insert("v7_job_sources", job_id=job_id, source="jobright", source_url_hash=hash_, gmail_message_id=message, seen_at=NOW)

    def tombstone(self, key, fuzzy, retired_days):
        self.db.insert("v7_tombstones", dedupe_key=key, fuzzy_key=fuzzy, retired_at=NOW - timedelta(days=retired_days), expires_at=NOW + timedelta(days=30), hits=0)

    def seed(self):
        c = self.cards
        self.source_row(self.job(c[0], "PUBLISHED"), MID, "h1")                                       # v1 owned
        self.source_row(self.job(c[1]), "other-mail", "h2")                                           # v2 the unique-URL collision: only another mail owns it
        self.job(c[2])                                                                                # v3 a job nobody owns
        self.tombstone(identity.url_hash(c[3].url), "a" * 64, 0)                                      # v4 retired today: suppressed by its URL, posted no later than the retirement
        self.tombstone("b" * 64, identity.fuzzy_key(c[4].company, c[4].title, c[4].location_text), 10)  # v5 the same opening retired 10 days ago; the card says it was posted today
        # v6: no job, no tombstone

    def trace(self, ref=REF, gmail=None):
        return cardtrace.trace(cardtrace._ReadOnly(gmail or self.box), self.db, ref, NOW)

    def test_each_card_gets_its_class_and_the_reason_a_tombstoned_one_was_suppressed(self):
        self.seed()
        body = self.trace()["card_trace"]
        self.assertEqual([c["class"] for c in body["cards"]], ["owned", "collision", "orphan", "suppressed_url", "suppressed_opening", "missing"])
        self.assertEqual((body["parsed"], body["source_rows"], body["job_less_rows"]), (6, 1, 0))
        self.assertEqual(body["by_class"], {"collision": 1, "missing": 1, "orphan": 1, "owned": 1, "suppressed_opening": 1, "suppressed_url": 1})
        by_n = {c["n"]: c for c in body["cards"]}
        self.assertEqual((by_n[1]["own_row"], by_n[1]["status"], by_n[1]["other_mails"]), (True, "PUBLISHED", 0))
        self.assertEqual((by_n[2]["own_row"], by_n[2]["other_mails"]), (False, 1))
        self.assertEqual((by_n[4]["tombstone"], by_n[4]["relisted"]), ("url", False))                 # would stay suppressed: not posted after the retirement
        self.assertEqual((by_n[5]["tombstone"], by_n[5]["relisted"]), ("opening", True))              # a newer posting of a retired opening: the case the posted-date bypass is for
        self.assertEqual((by_n[6]["job"], by_n[6]["tombstone"], by_n[6]["relisted"]), (False, "none", None))

    def test_a_job_less_row_left_for_a_suppressed_card_is_counted_and_marks_the_card_accounted(self):
        self.seed()
        self.source_row(None, MID, identity.url_hash(MID + "|" + self.cards[3].url))
        body = self.trace()["card_trace"]
        self.assertEqual((body["source_rows"], body["job_less_rows"]), (2, 1))
        self.assertTrue(body["cards"][3]["own_row"])

    def test_nothing_that_identifies_the_mail_or_a_vacancy_is_printed(self):
        self.seed()
        text = json.dumps(self.trace())
        for secret in (MID, "other-mail", "Acme", "Globex", "Initech", "jobright.ai", "https://", "alerts@"):
            self.assertNotIn(secret, text)
        allowed = {"n", "source", "class", "job", "status", "own_row", "other_mails", "tombstone", "relisted"}
        self.assertEqual({key for c in json.loads(text)["card_trace"]["cards"] for key in c}, allowed)

    def test_gmail_is_reachable_only_to_list_ids_and_read_one_message(self):
        self.assertEqual({n for n in vars(cardtrace._ReadOnly(self.box)) if not n.startswith("_")}, {"list_ids", "message"})
        self.assertFalse(hasattr(cardtrace._ReadOnly(self.box), "relabel"))
        tree = ast.parse(Path(cardtrace.__file__).read_text())
        used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertFalse(used & {"relabel", "label_id", "send_message", "trash", "delete", "commit", "executemany"})       # no Gmail write and no store write is even named
        statements = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lstrip().split(" ")[0].isupper()]
        self.assertTrue(statements and all(text.lstrip().startswith("SELECT") for text in statements if re.match(r"\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE)", text)))

    def test_it_fails_closed_with_fixed_codes(self):
        for ref in ("", "not-a-hash", MID, REF.upper(), REF[:63]):
            with self.assertRaises(store.StoreError) as raised:
                self.trace(ref)
            self.assertEqual(str(raised.exception), "NEWSLETTER_TRACE_MESSAGE_REF_INVALID")
        with self.assertRaises(store.StoreError) as raised:
            self.trace("0" * 64)
        self.assertEqual(str(raised.exception), "NEWSLETTER_TRACE_MESSAGE_NOT_FOUND")
        with self.assertRaises(store.StoreError) as raised:
            self.trace(gmail=FailingGmail())
        self.assertEqual(str(raised.exception), "NEWSLETTER_TRACE_GMAIL_FAILED")
        self.box.messages[MID] = ("someone@example.com", "<p>hi</p>", time.time())
        with self.assertRaises(store.StoreError) as raised:
            self.trace()
        self.assertEqual(str(raised.exception), "NEWSLETTER_TRACE_UNSUPPORTED_SENDER")
        with self.assertRaises(store.StoreError) as raised:
            cardtrace.run(10, True)
        self.assertEqual(str(raised.exception), "NEWSLETTER_TRACE_READ_ONLY")

    def test_it_only_ever_runs_selects(self):
        self.seed()
        seen = []
        real = self.db.cursor

        class Spy:
            def __init__(self, cursor):
                self.cursor = cursor

            def execute(self, sql, args=()):
                seen.append(sql)
                return self.cursor.execute(sql, args)

            def __getattr__(self, name):
                return getattr(self.cursor, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        with mock.patch.object(self.db, "cursor", lambda: Spy(real())):
            self.trace()
        self.assertTrue(seen and all(re.match(r"\s*SELECT", sql) for sql in seen))
