"""MAIL-1.2: Physical Mail evidence -> deterministic parse/reconcile -> private derivative state -> Mail Alerts card."""
import copy
import inspect
import json
import pathlib
import re
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from lifeos.physmail import card, chains, events, stage, state
from lifeos.platform import report_region, router
from lifeos.platform.gmail import GmailError, error_code
from tests.kit.notion import MailRegions

AT = chr(64)
VENDOR = f"notices{AT}{events.DOMAIN}"
NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
CT = ZoneInfo("America/Chicago")


def mail(message_id, subject, body="", at="2026-10-05T14:00:00+00:00", sender=VENDOR):
    return {"id": message_id, "sender": sender, "subject": subject, "received_at": at, "body_text": body, "label_ids": ["INBOX"]}


def actions(message_id, *lines, at="2026-10-05T14:00:00+00:00"):
    return mail(message_id, "Completed Action Requests", "\n".join(lines), at)


class ParseTests(unittest.TestCase):
    def test_new_mail_count_from_the_subject_or_the_body_and_the_portal_link(self):
        got = events.extract(mail("m1", "New Mail (3)", f"Sign in at https://app.{events.DOMAIN}/inbox to see them."))
        self.assertEqual((got["kind"], got["count"], got["portal"]), ("ARRIVAL", 3, f"https://app.{events.DOMAIN}/inbox"))
        self.assertEqual(events.extract(mail("m2", "New Mail", "You have 2 new items waiting."))["count"], 2)
        self.assertIsNone(events.extract(mail("m3", "New Mail (1)", "https://elsewhere.example/x"))["portal"])      # only the vendor's own https link is kept

    def test_each_scoped_verb_and_several_lines_in_one_notice(self):
        got = events.extract(actions("a1", "Mail #4801: Scan completed", "Item 4802 was shredded", "Mail 4803 recycled", "Item #4804 - forwarded"))
        self.assertEqual((got["kind"], got["actions"]), ("ACTIONS", [("4801", "SCAN"), ("4802", "SHRED"), ("4803", "RECYCLE"), ("4804", "FORWARD")]))

    def test_unknown_empty_or_contradictory_evidence_is_review_with_a_fixed_reason(self):
        cases = [(mail("r1", "New Mail", "nothing countable"), "COUNT_MISSING"), (mail("r2", "New Mail (3)", "You have 5 new items"), None),
                 (mail("r3", "New Mail (999)"), "COUNT_INVALID"), (mail("r4", "New Mail", "2 items and 4 items"), "COUNT_CONFLICT"),
                 (actions("r5"), "NO_LINES"), (actions("r6", "Mail #4801: opened"), "UNKNOWN_VERB"), (actions("r7", "- scanned the thing"), "ID_MISSING"),
                 (actions("r8", "Mail #4801 and #4802 shredded"), "ID_AMBIGUOUS"), (actions("r9", "Mail #4801 scanned and shredded"), "VERB_AMBIGUOUS"),
                 (mail("r10", "New Mail (2)", at="2026-10-05T14:00:00"), "MESSAGE_INCOMPLETE"), (None, "MESSAGE_INVALID")]
        for message, reason in cases:
            got = events.extract(message)
            if reason:
                self.assertEqual((got["kind"], got["reason"]), ("REVIEW", reason), reason)
        self.assertEqual(events.extract(mail("r2", "New Mail (3)", "You have 5 new items"))["count"], 3)              # the subject count wins; the body is only a fallback
        self.assertNotIn("nothing countable", json.dumps(events.extract(mail("r1", "New Mail", "nothing countable"))))      # no body text ever leaves the parser

    def test_other_senders_lookalikes_and_other_subjects_are_ignored_and_counted(self):
        for sender in (f"x{AT}{events.DOMAIN}.evil.example", f"x{AT}mail.{events.DOMAIN}", f"x{AT}example.com", "Display Name <>"):
            self.assertEqual(events.extract(mail("i1", "New Mail (1)", sender=sender))["kind"], "IGNORED")
        self.assertEqual(events.extract(mail("i2", "Your monthly statement"))["reason"], "SUBJECT_NOT_SCOPED")


class ChainTests(unittest.TestCase):
    def test_the_fold_and_every_contradiction(self):
        t = lambda n, v: (f"2026-10-0{n}T00:00:00+00:00", v)
        self.assertEqual(chains.fold([t(1, "SCAN")]), chains.OPEN)
        self.assertEqual(chains.fold([t(1, "SCAN"), t(2, "SHRED")]), chains.DONE)
        self.assertEqual(chains.fold([t(1, "RECYCLE"), t(2, "RECYCLE")]), chains.DONE)
        self.assertEqual(chains.fold([t(1, "FORWARD")]), chains.WAITING)
        for bad in ([t(1, "SHRED"), t(2, "RECYCLE")], [t(1, "SHRED"), t(2, "FORWARD")], [t(1, "FORWARD"), t(2, "SHRED")], [t(1, "SHRED"), t(2, "SCAN")]):
            self.assertEqual(chains.fold(bad), chains.REVIEW)
        self.assertEqual(chains.fold([t(3, "SHRED"), t(1, "SCAN")]), chains.DONE)                                    # order of arrival does not matter


class StateTests(unittest.TestCase):
    def fresh(self, anchor=11):
        return state.new_state(anchor, NOW)

    def test_binding_by_count_and_the_pending_unidentified_number(self):
        st, delta = state.apply(self.fresh(), [events.extract(mail("n1", "New Mail (3)")), events.extract(actions("a1", "Mail #4801 scanned", "Mail #4802 shredded"))], NOW)
        facts = state.summary(st, NOW)
        self.assertEqual((facts["unidentified"], facts["opened"], facts["done"], delta["arrivals_new"], delta["actions_new"]), (12, 1, 1, 1, 2))    # 11 + 3 units - 2 bound

    def test_replay_changes_nothing_and_notices_before_the_anchor_are_ignored(self):
        batch = [events.extract(mail("n1", "New Mail (3)")), events.extract(actions("a1", "Mail #4801 scanned")), events.extract(mail("old", "New Mail (9)", at="2026-10-03T12:00:00+00:00"))]
        once, first = state.apply(self.fresh(), batch, NOW)
        twice, second = state.apply(once, batch, NOW)
        self.assertEqual(once, twice)
        self.assertEqual((first["before_anchor"], second["arrivals_new"], second["actions_new"]), (1, 0, 0))

    def test_an_unbound_item_is_tolerated_only_inside_the_grace_period(self):
        early = [events.extract(actions("a1", "Mail #1 scanned".replace("#1", "#4801"), at="2026-10-06T10:00:00+00:00"), ), events.extract(actions("a2", "Mail #4802 scanned", at="2026-10-06T11:00:00+00:00"))]
        st, _ = state.apply(self.fresh(1), early, NOW)                                    # 1 unit, 2 chains: one excess, first seen inside the 14-day grace
        self.assertIsNone(state.summary(st, NOW)["degraded_reason"])
        late = [events.extract(actions("a3", "Mail #4803 scanned", at="2026-11-01T10:00:00+00:00"))]
        st, _ = state.apply(st, late, NOW)
        self.assertEqual(state.summary(st, NOW)["degraded_reason"], "UNBOUND_ITEM")

    def test_waiting_for_tracking_shows_the_aged_note_after_thirty_days(self):
        st, _ = state.apply(self.fresh(), [events.extract(actions("a1", "Mail #4801 forwarded", at="2026-10-05T10:00:00+00:00"))], NOW)
        self.assertEqual((state.summary(st, NOW)["waiting_tracking"], state.summary(st, NOW)["aged"]), (1, 0))
        self.assertEqual(state.summary(st, NOW + timedelta(days=31))["aged"], 1)

    def test_review_evidence_is_kept_by_id_and_counted_once(self):
        bad = events.extract(actions("r1", "Mail #4801 opened"))
        st, d1 = state.apply(self.fresh(), [bad], NOW)
        st, d2 = state.apply(st, [bad], NOW)
        self.assertEqual((state.summary(st, NOW)["review"], d1["review_new"], d2["review_new"]), (1, 1, 0))

    def test_a_bad_anchor_is_refused(self):
        for value in (-1, 1000, "11", None):
            with self.assertRaises(state.StateError):
                state.new_state(value, NOW)


class FakeStore:
    def __init__(self, row=None):
        self.row, self.saves = row, 0

    def ensure(self, connection):
        pass

    def load(self, connection, key):
        return copy.deepcopy(self.row)

    def save_verified(self, connection, key, snapshot, fail):
        self.saves += 1
        self.row = copy.deepcopy(snapshot)
        if self.load(connection, key) != snapshot:
            raise fail("READBACK_MISMATCH")


class FakeGmail:
    """Only the two read calls exist: any modify, label or relabel attempt is an AttributeError."""

    def __init__(self, messages, list_error=None, read_error=None):
        self.messages, self.list_error, self.read_error, self.queries = {m["id"]: m for m in messages}, list_error, read_error, []

    def list_ids_complete(self, query, limit):
        self.queries.append((query, limit))
        if self.list_error:
            raise GmailError(self.list_error)
        return list(self.messages)

    def message_record(self, message_id, keep_lines=False):
        if self.read_error == message_id:
            raise GmailError("GMAIL_MESSAGE_INCOMPLETE")
        return copy.deepcopy(self.messages[message_id])


class StageTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self._saved, stage.STORE = stage.STORE, self.store

    def tearDown(self):
        stage.STORE = self._saved

    def run_stage(self, messages=(), live=True, env=None, **kw):
        gmail = kw.pop("gmail", None) or FakeGmail(messages)
        counts = stage.run(1000, live, environ=env if env is not None else {"PHYSICAL_MAIL_ANCHOR": "11"}, gmail=gmail, connection=object(), now=NOW)
        return counts, gmail

    def test_first_run_needs_the_anchor_then_replays_from_the_anchor_date_and_saves_with_read_back(self):
        counts, gmail = self.run_stage([mail("n1", "New Mail (3)"), actions("a1", "Mail #4801 scanned")])
        self.assertEqual((counts["listed"], counts["arrivals_new"], counts["actions_new"], counts["saved"], counts["state_unidentified"]), (2, 1, 1, 1, 13))
        after = int(state.ANCHOR_AT.timestamp())
        self.assertEqual(gmail.queries, [(f"(from:{events.DOMAIN} OR from:{events.FEDEX_SENDER}) after:{after}", 1000)])
        self.assertEqual((self.store.saves, self.store.row["anchor_count"], self.store.row["accepted_at"]), (1, 11, NOW.isoformat()))

    def test_without_state_or_anchor_nothing_is_saved_and_the_failure_is_a_fixed_code(self):
        with self.assertRaises(stage.PhysMailError) as caught:
            self.run_stage([mail("n1", "New Mail (3)")], env={})
        self.assertEqual(str(caught.exception), "PHYSMAIL_NO_STATE")
        self.assertEqual(self.store.saves, 0)

    def test_dry_run_saves_nothing_and_a_second_run_continues_from_the_stored_row_with_an_overlap(self):
        counts, _ = self.run_stage([mail("n1", "New Mail (3)")], live=False)
        self.assertEqual((counts["saved"], self.store.saves), (0, 0))
        self.run_stage([mail("n1", "New Mail (3)")])
        again = FakeGmail([mail("n1", "New Mail (3)")])
        counts, _ = self.run_stage(env={}, gmail=again)                                     # the anchor is no longer needed
        self.assertEqual((counts["arrivals_new"], counts["saved"]), (0, 1))
        floor = max(NOW - stage.OVERLAP, state.ANCHOR_AT)                                   # the overlap never reaches back before the anchor
        self.assertEqual(again.queries[0][0], f"(from:{events.DOMAIN} OR from:{events.FEDEX_SENDER}) after:{int(floor.timestamp())}")

    def test_an_incomplete_listing_or_an_unreadable_message_fails_closed_and_changes_nothing(self):
        self.run_stage([mail("n1", "New Mail (3)")])
        before = copy.deepcopy(self.store.row)
        for gmail in (FakeGmail([mail("n2", "New Mail (1)")], list_error="GMAIL_LIST_LIMIT_EXCEEDED"), FakeGmail([mail("n2", "New Mail (1)")], read_error="n2")):
            with self.assertRaises(stage.PhysMailError):
                self.run_stage(env={}, gmail=gmail)
        self.assertEqual(self.store.row, before)

    def test_a_gmail_failure_names_its_status_and_reason_never_the_url_or_message(self):
        http = GmailError("GET https://gmail.googleapis.com/gmail/v1/users/me -> HTTP 429 rateLimitExceeded")
        self.assertEqual(error_code(http), "HTTP_429_RATELIMITEXCEEDED")
        self.assertEqual(error_code(GmailError("GET https://gmail.googleapis.com/gmail/v1/users/me -> HTTP 404 ")), "HTTP_404")
        self.assertEqual(error_code(GmailError("GMAIL_MESSAGE_INCOMPLETE")), "GMAIL_MESSAGE_INCOMPLETE")
        self.assertEqual(error_code(GmailError("GET https://gmail.googleapis.com/x -> timeout")), "GMAIL_ERROR")
        self.run_stage([mail("n1", "New Mail (3)")])
        with self.assertRaises(stage.PhysMailError) as caught:
            self.run_stage(env={}, gmail=FakeGmail([mail("n2", "New Mail (1)")], read_error="n2"))
        self.assertEqual(str(caught.exception), "PHYSMAIL_GMAIL_READ_FAILED_GMAIL_MESSAGE_INCOMPLETE")

    def test_a_new_arrival_notice_pushes_to_the_phone_once_with_counts_only(self):
        pushed = []
        with mock.patch("lifeos.platform.alerts.ntfy", lambda topic, title, body, severity, post=None, channels=None: (pushed.append((topic, title, body)) or True)):
            env = {"PHYSICAL_MAIL_ANCHOR": "11", "NTFY_TOPIC": "t"}
            counts, _ = self.run_stage([mail("n1", "New Mail (3)")], env=env)
            self.assertEqual((counts["notified"], pushed), (True, [("t", "LIFE OS Physical Mail", "3 new mail items received")]))
            self.run_stage([mail("n1", "New Mail (3)")], env={"NTFY_TOPIC": "t"})                     # the same notice again: no second push
            self.run_stage([actions("a1", "Mail #4801 scanned")], env={"NTFY_TOPIC": "t"})             # an action notice is not a received alert
            self.assertEqual(len(pushed), 1)
            self.run_stage([mail("n2", "New Mail (1)")], env={"NTFY_TOPIC": "t"}, live=False)           # a dry run never pushes
            self.assertEqual(len(pushed), 1)
            self.run_stage([mail("n3", "New Mail (1)")], env={"NTFY_TOPIC": "t"})
            self.assertEqual(pushed[-1][2], "1 new mail item received")
        self.assertNotIn("4801", str(pushed))

    def test_review_evidence_reaches_the_state_not_a_guess(self):
        counts, _ = self.run_stage([actions("r1", "Mail #4801 opened")])
        self.assertEqual((counts["review_new"], counts["state_review"], counts["state_opened"]), (1, 1, 0))

    def test_the_package_never_modifies_labels_or_read_state(self):
        for path in pathlib.Path(stage.__file__).parent.glob("*.py"):
            text = path.read_text()
            for forbidden in ("apply_amazon", "relabel", "batchModify", "/modify", "label_id", "addLabelIds", "removeLabelIds"):
                self.assertNotIn(forbidden, text, f"{path.name}: {forbidden}")


class CardRenderTests(unittest.TestCase):
    def stored(self, **kw):
        st, _ = state.apply(state.new_state(11, NOW), kw.pop("extracted", []), NOW)
        st["accepted_at"] = kw.pop("accepted", NOW.isoformat())
        return st

    def lines(self, blocks):
        return [report_region.plain(b) for b in blocks]

    def test_fresh_state_says_what_is_waiting_and_links_the_portal(self):
        st = self.stored(extracted=[events.extract(mail("n1", "New Mail (3)", f"https://app.{events.DOMAIN}/in")), events.extract(actions("a1", "Mail #4801 forwarded", at="2026-10-05T10:00:00+00:00"))])
        blocks, counts = card.render(st, NOW)
        text = self.lines(blocks)
        self.assertTrue(text[0].startswith("Updated 10:00 AM CT · 14 waiting · 0 need review"), text[0])
        self.assertIn("13 new item(s) not yet identified by a mail number", text)
        self.assertIn("#4801  ·  forwarded Oct 5  ·  ⚠ Tracking unavailable — obtain from provider", text)
        self.assertEqual(blocks[-1]["paragraph"]["rich_text"][0]["text"]["link"]["url"], f"https://app.{events.DOMAIN}/")
        self.assertEqual((counts["status"], counts["waiting"]), ("fresh", 14))

    def test_missing_stale_failed_or_unbound_evidence_is_degraded_and_never_none(self):
        none_state, none_counts = card.render(None, NOW)
        self.assertTrue(self.lines(none_state)[0].startswith("DEGRADED · Physical Mail starting state unknown"))
        self.assertEqual(none_counts["reason"], "no_state")
        fresh = self.stored()
        failed, counts = card.render(fresh, NOW, sync_failed=True)
        self.assertIn("DEGRADED · Physical Mail sync failed", self.lines(failed)[0])
        self.assertIn("showing last accepted state", self.lines(failed)[0])
        self.assertEqual(counts["status"], "degraded")
        stale, _ = card.render(self.stored(accepted=(NOW - timedelta(hours=7)).isoformat()), NOW)
        self.assertIn("DEGRADED · Physical Mail evidence stale", self.lines(stale)[0])
        for blocks in (failed, stale, none_state):
            self.assertNotIn("No physical mail waiting", self.lines(blocks))

    def test_an_empty_fresh_state_is_a_real_empty_sentence(self):
        st, _ = state.apply(state.new_state(0, NOW), [], NOW)
        st["accepted_at"] = NOW.isoformat()
        self.assertIn("No physical mail waiting", self.lines(card.render(st, NOW)[0]))

    def test_the_heading_is_exactly_mail_alerts_and_has_one_owner(self):
        self.assertEqual((card.TITLE, router.MAIL_ALERTS_REGION), ("Mail Alerts", "Mail Alerts"))
        self.assertEqual(router.OWNERS["Mail Alerts"], "v7-mail-alerts")
        self.assertEqual(len(set(router.OWNERS.values())), len(router.OWNERS))


ENV = {"MAIL_ALERTS_CARD_BLOCK_ID": "mail-callout", "JIRA_CARD_BLOCK_ID": "jira-callout", "CALENDAR_CARD_BLOCK_ID": "calendar-callout", "BILLS_CARD_BLOCK_ID": "bills-callout",
       "AMAZON_CARD_BLOCK_ID": "amazon-callout", "ATTENTION_CARD_BLOCK_ID": "attention-callout"}


class CardRunTests(unittest.TestCase):
    def setUp(self):
        st, _ = state.apply(state.new_state(11, NOW), [events.extract(actions("a1", "Mail #4801 scanned"))], NOW)
        st["accepted_at"] = NOW.isoformat()
        self.store = FakeStore(st)
        self._saved, card.STORE = card.STORE, self.store

    def tearDown(self):
        card.STORE = self._saved

    def go(self, notion=None, live=True, env=None):
        notion = notion or MailRegions()
        return notion, card.run(0, live, environ={**ENV, **(env or {})}, connection=object(), client=notion, now=NOW)

    def test_live_write_replaces_only_the_old_text_keeps_the_non_text_block_and_every_other_region(self):
        notion = MailRegions()
        before = {k: notion.full_tree(k) for k in ("jira-callout", "calendar-callout", "bills-callout", "amazon-callout", "attention-callout")}
        _, counts = self.go(notion)
        kids = notion.children["mail-callout"]
        self.assertEqual(report_region.plain(kids[0]), "Mail Alerts")
        self.assertTrue(report_region.plain(kids[1]).startswith("Updated 10:00 AM CT · 11 waiting"))
        self.assertEqual(kids[-1]["id"], "mail-table-old")
        self.assertEqual(kids[1]["id"], "mail-status-old")                                    # the old line is rewritten in place, never shown beside the new one
        self.assertEqual([m for m, _ in notion.log if m == "DELETE"], [])
        self.assertEqual({k: notion.full_tree(k) for k in before}, before)
        self.assertEqual(counts["status"], "fresh")

    def test_dry_run_and_missing_config_write_nothing(self):
        notion, counts = self.go(live=False)
        self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE", "PATCH")], [])
        self.assertEqual(counts["blocks_written"], 0)
        self.assertEqual(card.run(0, True, environ={}, connection=object(), client=MailRegions())["status"], "not_configured")

    def test_a_failed_ingest_writes_the_degraded_line_with_the_last_accepted_state(self):
        notion, counts = self.go(env={"MAIL_SYNC_OUTCOME": "failure"})
        self.assertTrue(report_region.plain(notion.children["mail-callout"][1]).startswith("DEGRADED · Physical Mail sync failed"))
        self.assertEqual(counts["status"], "degraded")

    def test_wrong_target_or_missing_protected_ids_refuse_before_any_write(self):
        for env, code in (({"MAIL_ALERTS_CARD_BLOCK_ID": "bills-callout"}, "MAILALERTS_CARD_NOT_OWNED"), ({"MAIL_ALERTS_CARD_BLOCK_ID": "amazon-callout"}, "MAILALERTS_CARD_NOT_OWNED"),
                          ({"JIRA_CARD_BLOCK_ID": ""}, "MAILALERTS_PROTECTED_REGION_UNAVAILABLE"), ({"ATTENTION_CARD_BLOCK_ID": ""}, "MAILALERTS_PROTECTED_REGION_UNAVAILABLE")):
            notion = MailRegions()
            with self.assertRaises(card.CardError) as caught:
                self.go(notion, env=env)
            self.assertEqual(str(caught.exception), code)
            self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE")], [])

    def test_a_protected_region_changed_mid_write_fails_the_run(self):
        for op in ("PATCH", "APPEND"):                                                          # the in-place rewrite, then the extra lines
            for region in ("calendar-callout", "jira-callout", "bills-callout"):
                with self.assertRaises(card.CardError) as caught:
                    self.go(MailRegions(change_on=op, change_region=region))
                self.assertEqual(str(caught.exception), "MAILALERTS_PROTECTED_REGION_CHANGED")

    def test_the_card_never_reads_the_source_or_writes_state(self):
        text = inspect.getsource(card)
        for forbidden in ("Gmail", "save_verified", "STORE.save", "list_ids", "message_record"):
            self.assertNotIn(forbidden, text)


class WiringTests(unittest.TestCase):
    def test_the_cli_stages_exist_and_physical_mail_stays_out_of_attention(self):
        from lifeos import run
        from lifeos.attention import policy
        self.assertTrue({"mail-alerts", "mail-card"} <= set(run.STAGES))
        verdict = policy.decide(f"notices{AT}{events.DOMAIN}", "Anything at all")
        self.assertNotEqual(verdict[0], "ADMIT")

    def test_no_literal_vendor_domain_and_no_block_id_in_the_repository_text(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        for path in list((root / "lifeos" / "physmail").glob("*.py")) + [pathlib.Path(__file__)]:
            self.assertNotIn(events.DOMAIN, path.read_text(), path.name)
            self.assertIsNone(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", path.read_text()), path.name)


class ConnectionTests(unittest.TestCase):
    """Runs 39659585455 and 39668591536: the store connection opened for the stored state sat idle through the Gmail census and was lost at the first statement of the save."""

    class Db:
        def __init__(self):
            self.opened, self.closed = [], []

        def connect(self):
            outer = self

            class Manager:
                def __enter__(self):
                    self.conn = object()
                    outer.opened.append(self.conn)
                    return self.conn

                def __exit__(self, *exc):
                    outer.closed.append(self.conn)
            return Manager()

    class Store(FakeStore):
        def __init__(self):
            super().__init__()
            self.loaded_on, self.saved_on = [], []

        def load(self, connection, key):
            self.loaded_on.append(connection)
            return super().load(connection, key)

        def save_verified(self, connection, key, snapshot, fail):
            self.saved_on.append(connection)
            super().save_verified(connection, key, snapshot, fail)

    def run_stage(self, db, store, gmail=None):
        saved, stage.STORE = stage.STORE, store
        try:
            with mock.patch.object(stage.db, "connect", db.connect):
                return stage.run(1000, True, environ={"PHYSICAL_MAIL_ANCHOR": "11"}, gmail=gmail or FakeGmail([]), now=NOW)
        finally:
            stage.STORE = saved

    def test_the_save_uses_a_fresh_connection_and_the_idle_one_is_closed_first(self):
        db, store = self.Db(), self.Store()
        counts = self.run_stage(db, store)
        self.assertEqual((counts["saved"], len(db.opened)), (1, 2))
        self.assertIsNot(store.saved_on[0], store.loaded_on[0])                    # the connection that sat idle (the first load) is not the one that saves
        self.assertEqual(db.closed, db.opened)                                      # and every connection opened was closed, once, in order

    def test_a_failed_reconnect_leaves_no_double_close(self):
        db, store = self.Db(), self.Store()
        real = db.connect
        calls = []
        def connect():
            calls.append(1)
            if len(calls) == 2:
                raise OSError("tunnel down")
            return real()
        db.connect = connect
        with self.assertRaises(OSError):
            self.run_stage(db, store)
        self.assertEqual((len(db.opened), len(db.closed), store.saves), (1, 1, 0))     # nothing saved, the first closed once


class RuntimePathTests(unittest.TestCase):
    """PRE_MERGE_RUNTIME_GATE: ENTRY POINT -> REQUIRED INPUTS -> CALL CHAIN -> MUTATION OWNER -> SUCCESS / FAIL-CLOSED SIGNAL -> CANONICAL STATE -> READ-BACK, on the production composition."""

    def test_the_whole_path_runs_from_gmail_evidence_to_the_read_back_callout_and_ends_fresh(self):
        store = FakeStore()
        saved_stage, saved_card = stage.STORE, card.STORE
        stage.STORE = card.STORE = store
        try:
            gmail = FakeGmail([mail("n1", "New Mail (3)", f"https://app.{events.DOMAIN}/in"), actions("a1", "Mail #4801 scanned", "Mail #4802 forwarded", at="2026-10-05T15:00:00+00:00")])
            counts = stage.run(1000, True, environ={"PHYSICAL_MAIL_ANCHOR": "11"}, gmail=gmail, connection=object(), now=NOW)
            self.assertEqual((counts["saved"], counts["status"]), (1, "ok"))                       # SUCCESS SIGNAL of the ingest; the row is read back by save_verified
            notion = MailRegions()
            before = {k: notion.full_tree(k) for k in ("jira-callout", "calendar-callout", "bills-callout", "amazon-callout", "attention-callout")}
            out = card.run(0, True, environ={**ENV, "MAIL_SYNC_OUTCOME": "success"}, connection=object(), client=notion, now=NOW)
            self.assertEqual((out["status"], out["waiting"], out["blocks_written"] > 0), ("fresh", 14, True))      # the promised complete state: not DEGRADED
            kids = notion.children["mail-callout"]
            self.assertEqual(report_region.plain(kids[0]), "Mail Alerts")                          # AUTHORITATIVE READ-BACK ran inside replace_text and passed
            self.assertTrue(report_region.plain(kids[1]).startswith("Updated 10:00 AM CT · 14 waiting"))
            self.assertEqual({k: notion.full_tree(k) for k in before}, before)                     # no other Daily Report region changed
        finally:
            stage.STORE, card.STORE = saved_stage, saved_card

    def test_the_cli_entry_points_reach_the_changed_modules(self):
        from lifeos import run as runner
        self.assertEqual(runner.STAGES["mail-alerts"].target, ("lifeos.physmail.stage", "run"))
        self.assertEqual(runner.STAGES["mail-card"].target, ("lifeos.physmail.card", "run"))

    def test_the_domains_job_supplies_every_input_runs_ingest_then_card_and_waits_for_the_other_writers(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        domains = yaml.safe_load((root / "domains.yml").read_text())
        job = domains["jobs"]["mail"]
        self.assertEqual(set(job["needs"]), {"jira", "agenda", "bills", "amazon", "attention"})      # the card proves the other regions unchanged, so it runs after their writers
        ingest, card_step = [s for s in job["steps"] if s.get("id") in ("mail", "mcard")]
        self.assertTrue(ingest["run"].startswith("python -m lifeos.run mail-alerts"))
        self.assertTrue(card_step["run"].startswith("python -m lifeos.run mail-card"))
        self.assertEqual(card_step["env"]["MAIL_SYNC_OUTCOME"], "${{ steps.mail.outcome }}")          # a failed ingest is carried to the card as DEGRADED
        self.assertEqual(card_step["env"]["MAIL_ALERTS_CARD_BLOCK_ID"], "${{ secrets.MAIL_ALERTS_CARD_BLOCK_ID }}")
        self.assertEqual(ingest["env"]["PHYSICAL_MAIL_ANCHOR"], "${{ inputs.physical_mail_anchor }}")  # the first run's anchor is an input, never code
        self.assertTrue(ingest["continue-on-error"] and card_step["continue-on-error"])               # a failure is a warning, not a Jobs failure
        for name in ("physical_mail", "physical_mail_anchor"):
            self.assertIn(name, domains[True]["workflow_dispatch"]["inputs"])
        self.assertIn("inputs.physical_mail", domains["jobs"]["dcc"]["if"])                          # LIFE-95: a mail-only run refreshes the Command Center in the same run, through the existing job
        self.assertEqual(ingest["env"]["PHYSICAL_MAIL_EXPECT"], "${{ vars.PHYSICAL_MAIL_EXPECT }}")    # Jim's own tally (counts only) gates the one-time rebuild
        self.assertNotIn("mail", yaml.safe_load((root / "hourly.yml").read_text())["jobs"])          # no second scheduler, workflow family or hourly step
        self.assertNotIn("schedule", domains[True])

    def test_missing_input_fails_closed_without_a_write_and_the_card_then_says_degraded(self):
        store = FakeStore()
        saved_stage, saved_card = stage.STORE, card.STORE
        stage.STORE = card.STORE = store
        try:
            with self.assertRaises(stage.PhysMailError):
                stage.run(1000, True, environ={}, gmail=FakeGmail([]), connection=object(), now=NOW)       # no anchor, no state: FAIL-CLOSED SIGNAL
            self.assertEqual(store.saves, 0)
            notion = MailRegions()
            card.run(0, True, environ={**ENV, "MAIL_SYNC_OUTCOME": "failure"}, connection=object(), client=notion, now=NOW)
            self.assertTrue(report_region.plain(notion.children["mail-callout"][1]).startswith("DEGRADED · Physical Mail starting state unknown"))
        finally:
            stage.STORE, card.STORE = saved_stage, saved_card

    def test_the_state_table_is_the_one_snapshot_table_with_one_row(self):
        self.assertEqual((stage.STORE.table, stage.KEY), ("v7_physical_mail", 1))
        self.assertIn("CREATE TABLE IF NOT EXISTS v7_physical_mail", stage.STORE.schema[0])


# ---- LIFE-95: individual pieces, the HTML-only notices, the one-time rebuild ---------------------------------------------------------------------------------------------
import base64

from lifeos.platform import gmail as gmail_mod


def _b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode()


def html_only(html):
    return {"mimeType": "multipart/alternative", "parts": [{"mimeType": "text/html", "body": {"data": _b64(html)}}]}


def rows(numbers, verb):
    return "".join(f"<tr><td>Mail #{n}</td><td>{verb}</td></tr>" for n in numbers)


SCANNED = [5000000 + i for i in range(1, 10)]            # synthetic numbers only: nothing here is a real mail number
RECYCLED = [5000000 + i for i in range(10, 20)]
SHREDDED, FORWARDED = 5000020, 5000021
ANCHOR = 21


class HtmlGmail(FakeGmail):
    """Builds each message the way Gmail answers an HTML-only vendor notice and reads it through the real extraction, honouring keep_lines."""

    def __init__(self, notices, **kw):
        super().__init__([mail(mid, subject, "", at) for mid, subject, _, at in notices], **kw)
        self.payloads = {mid: payload for mid, _, payload, _ in notices}
        self.keep = []

    def message_record(self, message_id, keep_lines=False):
        record = super().message_record(message_id)
        self.keep.append(keep_lines)
        record["body_text"] = gmail_mod._find_text(self.payloads[message_id], keep_lines)
        return record


def census(first_at="2026-10-19T15:00:00+00:00"):
    scan_and_recycle = html_only("<html><body><p>Completed Action Requests</p><table>" + rows(SCANNED, "Scan") + rows(RECYCLED, "Recycle") + "</table></body></html>")
    shred = {"mimeType": "text/plain", "body": {"data": _b64(f"Mail #{SHREDDED}: Shred completed")}}
    forward = html_only(f"<html><body><ul><li>Item {FORWARDED} - Forward completed</li></ul></body></html>")
    return [("c1", "Completed Action Requests", scan_and_recycle, first_at), ("c2", "Completed Action Requests", shred, "2026-10-19T16:00:00+00:00"),
            ("c3", "Completed Action Requests", forward, "2026-10-20T15:00:00+00:00")]


class HtmlNoticeTests(unittest.TestCase):
    SENDER = VENDOR

    def extract(self, payload, keep):
        body = gmail_mod._find_text(payload, keep)
        return events.extract({"id": "m", "sender": self.SENDER, "subject": "Completed Action Requests", "received_at": "2026-10-19T15:00:00+00:00", "body_text": body})

    def test_an_html_only_multi_item_notice_keeps_one_action_per_line(self):
        payload = census()[0][2]
        got = self.extract(payload, True)
        self.assertEqual((got["kind"], len(got["actions"])), ("ACTIONS", 19))
        self.assertEqual({verb for _, verb in got["actions"]}, {"SCAN", "RECYCLE"})
        self.assertEqual(sum(1 for _, verb in got["actions"] if verb == "SCAN"), 9)

    def test_the_mailbox_link_in_an_html_only_notice_is_found_only_when_lines_are_kept_and_is_never_an_action(self):
        link = f"https://box.{events.DOMAIN}/login?url=%2Fapp%2Fmail"
        arrival = html_only(f"<html><body><p>1 new mail item(s) have been added.</p><p><a href=\"{link}\">View Mail</a></p><p>Powered by <a href=\"https://www.{events.DOMAIN}\">x</a></p></body></html>")
        kept = events.extract({"id": "n", "sender": self.SENDER, "subject": "New Mail", "received_at": "2026-10-19T15:00:00+00:00", "body_text": gmail_mod._find_text(arrival, True)})
        self.assertEqual((kept["kind"], kept["portal"]), ("ARRIVAL", link))
        flat = events.extract({"id": "n", "sender": self.SENDER, "subject": "New Mail", "received_at": "2026-10-19T15:00:00+00:00", "body_text": gmail_mod._find_text(arrival, False)})
        self.assertIsNone(flat["portal"])                                                                  # every other consumer keeps the flat text, with no link targets
        done = html_only(f"<html><body><p>Mail #5000001: Forward Completed</p><p><a href=\"{link}\">Log In</a></p></body></html>")
        got = self.extract(done, True)
        self.assertEqual((got["kind"], got["actions"], got["portal"]), ("ACTIONS", [("5000001", "FORWARD")], link))       # a completed-action notice carries the login link too
        st, _ = state.apply(state.new_state(0, NOW), [{**got, "at": "2026-10-20T15:00:00+00:00"}], NOW)
        self.assertEqual(st["portal"], link)                                                                  # so the link is known even when no "New Mail" notice is in the read window

    def test_every_other_consumer_keeps_the_flat_body_and_plain_text_is_the_same_either_way(self):
        flat = self.extract(census()[0][2], False)
        self.assertEqual((flat["kind"], flat["reason"]), ("REVIEW", "ID_AMBIGUOUS"))                    # the default (Amazon, Attention, delivery census) is untouched
        plain = {"mimeType": "text/plain", "body": {"data": _b64("Mail #5000001: Scan completed\nMail #5000002: Scan completed")}}
        self.assertEqual(gmail_mod._find_text(plain, True), gmail_mod._find_text(plain, False))
        self.assertFalse(inspect.signature(gmail_mod.Gmail.message_record).parameters["keep_lines"].default)

    def test_genuine_ambiguity_stays_review_even_with_lines_kept(self):
        two = html_only("<table><tr><td>Mail #5000001 and #5000002</td><td>Scan</td></tr></table>")
        self.assertEqual(self.extract(two, True)["reason"], "ID_AMBIGUOUS")
        none = html_only("<table><tr><td>Mail waiting</td><td>Scan</td></tr><tr><td>- Scan</td></tr></table>")
        self.assertEqual(self.extract(none, True)["kind"], "REVIEW")


def piece_state(extracted, anchor=ANCHOR, accepted=True):
    st, _ = state.apply(state.new_state(anchor, NOW), extracted, NOW)
    st["accepted_at"] = NOW.isoformat() if accepted else None
    return st


class ItemLevelTests(unittest.TestCase):
    def lines(self, st, now=NOW, sync_failed=False):
        blocks, counts = card.render(st, now, sync_failed)
        return [report_region.plain(b) for b in blocks], blocks, counts

    def full(self):
        return piece_state([events.extract(mail(mid, subject, "\n".join(f"Mail #{n}: {v}" for n, v in body), at)) for mid, subject, body, at in [
            ("c1", "Completed Action Requests", [(n, "Scan completed") for n in SCANNED] + [(n, "Recycle completed") for n in RECYCLED], "2026-10-19T15:00:00+00:00"),
            ("c2", "Completed Action Requests", [(SHREDDED, "Shred completed")], "2026-10-19T16:00:00+00:00"),
            ("c3", "Completed Action Requests", [(FORWARDED, "Forward completed")], "2026-10-20T15:00:00+00:00")]])

    def test_the_expected_reconciliation_and_ten_individual_lines(self):
        st = self.full()
        facts = state.summary(st, NOW)
        self.assertEqual((facts["identified"], facts["opened"], facts["done"], facts["waiting_tracking"], facts["unidentified"], facts["review"]), (21, 9, 11, 1, 0, 0))
        text, blocks, counts = self.lines(st)
        self.assertEqual(text[0], "Updated 10:00 AM CT · 10 waiting · 0 need review")                   # the first line keeps the shape the Command Center and Attention read
        items = [t for t in text if t.startswith("#")]
        self.assertEqual(len(items), 10)
        self.assertEqual([t.split(" ")[0] for t in items], [f"#{FORWARDED}"] + [f"#{n}" for n in sorted(SCANNED, reverse=True)])      # forwarded first, then newest number first
        self.assertTrue(all(b["type"] == "bulleted_list_item" for b in blocks if report_region.plain(b).startswith("#")))
        self.assertEqual(items[1], f"#{SCANNED[-1]}  ·  scanned Oct 19")
        self.assertEqual(counts["listed"], 10)
        self.assertEqual([t for t in text if t[:1] in "🚚📥"], ["🚚 Forwarded — not delivered until tracking says so (1)", "📥 Scanned — decide: forward, shred or recycle (9)"])      # the shared next action is said once per group
        self.assertEqual(text[-1], "Last 7 days: 10 recycled, 1 shredded")        # history sits last, muted

    def test_each_mail_number_is_one_line_and_disposed_pieces_are_never_listed(self):
        text, _, _ = self.lines(self.full())
        listed = " ".join(t for t in text if t.startswith("#"))
        for n in RECYCLED + [SHREDDED]:
            self.assertNotIn(str(n), listed)
        self.assertEqual(len({t.split(" ")[0] for t in text if t.startswith("#")}), 10)

    def test_a_piece_moves_through_scan_forward_and_disposal_as_evidence_arrives(self):
        e = lambda mid, line, at: events.extract(mail(mid, "Completed Action Requests", line, at))
        st = piece_state([e("a", f"Mail #{SCANNED[0]}: Scan completed", "2026-10-05T10:00:00+00:00")])
        self.assertIn(f"#{SCANNED[0]}  ·  scanned Oct 5", self.lines(st)[0])
        st, _ = state.apply(st, [e("b", f"Mail #{SCANNED[0]}: Forward completed", "2026-10-06T10:00:00+00:00")], NOW)
        self.assertTrue(any(t.startswith(f"#{SCANNED[0]}  ·  forwarded Oct 6  ·  ⚠ Tracking unavailable") for t in self.lines(st)[0]))
        st, _ = state.apply(st, [e("c", f"Mail #{SCANNED[0]}: Recycle completed", "2026-10-07T10:00:00+00:00")], NOW)    # forward then recycle contradict: REVIEW, never guessed
        self.assertTrue(any(t.startswith(f"#{SCANNED[0]}  ·  last notice") for t in self.lines(st)[0]))
        other = piece_state([e("d", f"Mail #{SCANNED[1]}: Scan completed", "2026-10-05T10:00:00+00:00"), e("e", f"Mail #{SCANNED[1]}: Recycle completed", "2026-10-06T10:00:00+00:00")])
        self.assertFalse(any(t.startswith("#") for t in self.lines(other)[0]))
        self.assertIn("Last 7 days: 1 recycled", " ".join(self.lines(other)[0]))

    def test_tracking_is_flagged_when_absent_and_shown_with_its_own_evidence_when_present(self):
        st = self.full()
        text, blocks, _ = self.lines(st)
        flagged = next(t for t in text if t.startswith(f"#{FORWARDED}"))
        self.assertIn("Tracking unavailable — obtain from provider", flagged)
        self.assertIn("not delivered until tracking says so", "\n".join(self.lines(st)[0]))
        self.assertNotIn("Delivered", flagged)                                                              # forward completed is never delivery
        st["chains"][str(FORWARDED)]["tracking"] = {"carrier": "ExampleCarrier", "number": "TRK0001", "url": "https://example.invalid/t/TRK0001", "state": "In transit"}
        text, blocks, _ = self.lines(st)
        shown = next(t for t in text if t.startswith(f"#{FORWARDED}"))
        self.assertEqual(shown, f"#{FORWARDED}  ·  forwarded Oct 20  ·  ExampleCarrier TRK0001  ·  In transit")
        block = next(b for b in blocks if report_region.plain(b).startswith(f"#{FORWARDED}"))
        self.assertEqual([p["text"]["link"]["url"] for p in block["bulleted_list_item"]["rich_text"] if p["text"].get("link")], ["https://example.invalid/t/TRK0001"])
        st["chains"][str(FORWARDED)]["tracking"] = {"carrier": "x", "number": "  "}                          # a blank number is no tracking
        self.assertIn("Tracking unavailable", next(t for t in self.lines(st)[0] if t.startswith(f"#{FORWARDED}")))

    def test_tracking_is_recorded_with_its_piece_shown_as_written_and_a_delivery_leaves_the_list(self):
        st = self.full()
        entry = {"bundle": "Bundle A", "carrier": "ExampleCarrier", "service": "Example Overnight", "number": "TRK000002", "url": "https://example.invalid/t/TRK000002"}
        for bad in ({"number": "  "}, {"number": "x"}, {"number": "TRK000002", "url": "http://insecure.invalid"}):          # nothing is stored that is not a plain tracking number
            self.assertFalse(state.record_tracking(st, FORWARDED, bad, "operator", NOW)[1])
        self.assertFalse(state.record_tracking(st, SCANNED[0], entry, "operator", NOW)[1])                         # only a forwarded piece takes tracking
        self.assertFalse(state.record_tracking(st, "999", entry, "operator", NOW)[1])
        st, recorded = state.record_tracking(st, FORWARDED, entry, "operator", NOW)
        self.assertTrue(recorded)
        self.assertFalse(state.record_tracking(st, FORWARDED, entry, "operator", NOW)[1])                         # idempotent
        text, blocks, _ = self.lines(st)
        self.assertEqual(next(t for t in text if t.startswith(f"#{FORWARDED}")), f"#{FORWARDED}  ·  forwarded Oct 20  ·  Bundle A Example Overnight TRK000002  ·  no delivery status yet")
        st["chains"][str(FORWARDED)]["tracking"]["delivered"] = "2026-10-22"
        text, _, counts = self.lines(st, now=datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc))
        self.assertFalse(any(t.startswith(f"#{FORWARDED}") for t in text))
        self.assertTrue(text[0].endswith("9 waiting · 0 need review"))
        self.assertEqual(counts["waiting"], 9)
        self.assertEqual(text[-1], "Last 7 days: 10 recycled, 1 shredded, 1 delivered")

    def test_the_portal_link_is_the_notices_own_host_and_the_operator_entry_reaches_the_stage_and_survives_a_rebuild(self):
        st = self.full()
        st["portal"] = "https://box.example.invalid/login?url=%2Fapp%2Fmail%2Fcompleted"
        blocks, _ = card.render(st, NOW)
        link = next(b for b in blocks if report_region.plain(b) == "Open the mailbox portal")
        self.assertEqual(link["paragraph"]["rich_text"][0]["text"]["link"]["url"], "https://box.example.invalid/")
        self.assertEqual(stage._entered_tracking({"PHYSICAL_MAIL_TRACKING_ENTRY": f"{FORWARDED} | FedEx | Overnight | 123456789012 | Bundle 1"}),
                         (str(FORWARDED), {"carrier": "FedEx", "service": "Overnight", "number": "123456789012", "bundle": "Bundle 1", "url": "https://www.fedex.com/fedextrack/?trknbr=123456789012"}))
        for bad in ("", "x | a | b | c", f"{FORWARDED} | only | three"):
            self.assertIsNone(stage._entered_tracking({"PHYSICAL_MAIL_TRACKING_ENTRY": bad}))

    def test_disposed_and_delivered_pieces_stop_being_mentioned_after_seven_days_but_stay_in_the_history(self):
        st = self.full()
        later = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)
        text = self.lines(st, now=later)[0]
        self.assertFalse(any(t.startswith("Last 7 days") for t in text))
        self.assertEqual(state.disposed(st), {"recycled": 10, "shredded": 1})

    def test_an_unreadable_source_keeps_the_last_accepted_pieces_under_a_degraded_line(self):
        text, _, counts = self.lines(self.full(), sync_failed=True)
        self.assertTrue(text[0].startswith("DEGRADED · Physical Mail sync failed"))
        self.assertEqual(sum(1 for t in text if t.startswith("#")), 10)
        self.assertEqual(counts["status"], "degraded")

    def test_a_long_pile_ends_with_a_count_not_a_silent_cut(self):
        many = piece_state([events.extract(mail("big", "Completed Action Requests", "\n".join(f"Mail #{6000000 + i}: Scan completed" for i in range(40))))], anchor=40)
        text, _, counts = self.lines(many)
        self.assertEqual(sum(1 for t in text if t.startswith("#")), card.MAX_ITEMS)
        self.assertIn("+10 more active pieces: open the mailbox portal", text)
        self.assertTrue(text[0].startswith("Updated 10:00 AM CT · 40 waiting"))

    def test_the_item_lines_are_written_and_read_back_natively_and_the_rest_of_the_page_is_untouched(self):
        st = self.full()
        store = FakeStore(st)
        saved, card.STORE = card.STORE, store
        try:
            notion = MailRegions()
            before = {k: notion.full_tree(k) for k in ("jira-callout", "calendar-callout", "bills-callout", "amazon-callout", "attention-callout")}
            out = card.run(0, True, environ=dict(ENV), connection=object(), client=notion, now=NOW)
            kids = notion.children["mail-callout"]
            self.assertEqual(report_region.plain(kids[0]), "Mail Alerts")
            written = [report_region.plain(k) for k in kids[1:]]
            self.assertEqual(sum(1 for t in written if t.startswith("#")), 10)
            self.assertEqual(kids[-1]["id"], "mail-table-old")                                              # Jim's own non-text block stays last
            self.assertEqual({k: notion.full_tree(k) for k in before}, before)
            again = card.run(0, True, environ=dict(ENV), connection=object(), client=notion, now=NOW)       # idempotent: the same lines, no duplicates
            self.assertEqual([report_region.plain(k) for k in notion.children["mail-callout"][1:]], written)
            self.assertEqual(out["listed"], again["listed"])
        finally:
            card.STORE = saved


class ReviewReplayTests(unittest.TestCase):
    def test_a_later_clean_parse_resolves_that_message_only(self):
        st = state.new_state(21, NOW)
        st["review"] = [{"id": "c1", "at": "2026-10-19T15:00:00+00:00", "reason": "ID_AMBIGUOUS"}, {"id": "other", "at": "2026-10-19T15:00:00+00:00", "reason": "COUNT_MISSING"}]
        good = events.extract(actions("c1", "Mail #5000001: Scan completed", at="2026-10-19T15:00:00+00:00"))
        merged, delta = state.apply(st, [good], NOW)
        self.assertEqual([r["id"] for r in merged["review"]], ["other"])
        self.assertEqual((delta["review_resolved"], state.summary(merged, NOW)["review"]), (1, 1))
        again, second = state.apply(merged, [good], NOW)
        self.assertEqual((again, second["review_resolved"]), (merged, 0))                                    # idempotent
        still = events.extract(actions("c1", "Mail #5000001 and #5000002 scanned", at="2026-10-19T15:00:00+00:00"))
        kept, _ = state.apply(st, [still], NOW)
        self.assertEqual([r["id"] for r in kept["review"]], ["c1", "other"])                                 # a message that still does not parse is not resolved


class RebuildTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self._saved, stage.STORE = stage.STORE, self.store

    def tearDown(self):
        stage.STORE = self._saved

    def old_row(self):
        """What the flat parser left: the same anchor, the chains it did see, and the notices it sent to review."""
        st, _ = state.apply(state.new_state(ANCHOR, NOW), [events.extract(actions("c2", f"Mail #{SHREDDED}: Shred completed", at="2026-10-19T16:00:00+00:00")),
                                                           events.extract(actions("c3", f"Mail #{FORWARDED}: Forward completed", at="2026-10-20T15:00:00+00:00")),
                                                           events.extract(actions("c1", "Mail #5000001 and #5000002 scanned", at="2026-10-19T15:00:00+00:00"))], NOW)
        st["accepted_at"], st["through"] = NOW.isoformat(), NOW.isoformat()
        st.pop("replay", None)
        return st

    def go(self, notices=None, live=True, env=None):
        gmail = HtmlGmail(notices or census())
        counts = stage.run(1000, live, environ=env or {}, gmail=gmail, connection=object(), now=NOW)
        return counts, gmail

    def test_the_whole_census_is_replayed_from_the_anchor_and_replaces_the_row_only_after_proof(self):
        self.store.row = self.old_row()
        old = copy.deepcopy(self.store.row)
        counts, gmail = self.go(env={"PHYSICAL_MAIL_EXPECT": "21,9,11,1,0,0"})
        self.assertEqual((counts["rebuild"], counts["saved"], counts["state_identified"], counts["state_opened"], counts["state_done"], counts["state_waiting_tracking"],
                          counts["state_unidentified"], counts["state_review"]), (1, 1, 21, 9, 11, 1, 0, 0))
        self.assertEqual(self.store.row["replay"], state.REPLAY)
        self.assertEqual(self.store.row["prior"], old)                                                       # the known-good row is carried for a rollback
        self.assertNotIn("prior", self.store.row["prior"])
        self.assertEqual(self.store.row["review"], [])
        self.assertTrue(all(gmail.keep))                                                                      # the stage asked for lines
        self.assertNotIn("notified", counts)                                                                  # a rebuild never pushes old arrivals to the phone

    def test_the_second_run_is_an_ordinary_overlap_run_and_changes_nothing(self):
        self.store.row = self.old_row()
        self.go()
        after = copy.deepcopy(self.store.row)
        counts, gmail = self.go()
        self.assertEqual((counts["rebuild"], counts["actions_new"], counts["review_new"]), (0, 0, 0))
        self.assertEqual({k: v for k, v in self.store.row.items() if k not in ("taken_at", "accepted_at", "through")}, {k: v for k, v in after.items() if k not in ("taken_at", "accepted_at", "through")})
        self.assertTrue(any("after:" in q for q, _ in gmail.queries))

    def test_a_candidate_that_does_not_match_jims_tally_or_loses_a_number_is_rejected_and_the_row_stays(self):
        self.store.row = self.old_row()
        old = copy.deepcopy(self.store.row)
        with self.assertRaises(stage.PhysMailError) as error:
            self.go(env={"PHYSICAL_MAIL_EXPECT": "21,9,12,0,0,0"})
        self.assertIn("REBUILD_REJECTED", str(error.exception))
        self.assertEqual((self.store.saves, self.store.row), (0, old))
        fewer = [n for n in census() if n[0] != "c3"]                                                         # the forward notice is missing: a number the row knew would be lost
        with self.assertRaises(stage.PhysMailError):
            self.go(notices=fewer)
        self.assertEqual((self.store.saves, self.store.row), (0, old))
        with self.assertRaises(stage.PhysMailError) as bad:
            self.go(env={"PHYSICAL_MAIL_EXPECT": "1,2"})
        self.assertIn("EXPECT_INVALID", str(bad.exception))

    def test_an_unreadable_message_or_a_dry_run_changes_nothing(self):
        self.store.row = self.old_row()
        old = copy.deepcopy(self.store.row)
        gmail = HtmlGmail(census(), read_error="c2")
        with self.assertRaises(stage.PhysMailError):
            stage.run(1000, True, environ={}, gmail=gmail, connection=object(), now=NOW)
        counts, _ = self.go(live=False)
        self.assertEqual((counts["saved"], counts["rebuild"], counts["state_review"]), (0, 1, 0))             # a dry run shows the candidate's counts and writes nothing
        self.assertEqual((self.store.saves, self.store.row), (0, old))

    def test_a_first_run_is_already_a_full_replay_and_needs_no_rebuild(self):
        counts, _ = self.go(env={"PHYSICAL_MAIL_ANCHOR": str(ANCHOR)})
        self.assertEqual((counts["rebuild"], counts["state_identified"], counts["saved"]), (0, 21, 1))
        self.assertEqual(self.store.row["replay"], state.REPLAY)
        self.assertNotIn("prior", self.store.row)

    def test_tracking_recorded_on_a_piece_survives_the_rebuild_and_a_one_off_entry_is_saved_with_it(self):
        self.store.row = self.old_row()
        self.store.row["chains"][str(FORWARDED)] = {"events": {"c3:FORWARD": ["2026-10-20T15:00:00+00:00", "FORWARD"]}, "tracking": {"number": "TRK000003", "carrier": "ExampleCarrier"}}
        self.go()
        self.assertEqual(self.store.row["chains"][str(FORWARDED)]["tracking"]["number"], "TRK000003")
        self.go(env={"PHYSICAL_MAIL_TRACKING_ENTRY": f"{FORWARDED} | ExampleCarrier | Overnight | TRK000004 | Bundle 2"})
        self.assertEqual(self.store.row["chains"][str(FORWARDED)]["tracking"]["number"], "TRK000004")
        self.assertEqual(self.store.row["chains"][str(FORWARDED)]["tracking"]["source"], "operator")

    def test_the_rebuild_is_read_only_toward_gmail(self):
        self.assertNotIn("modify", inspect.getsource(stage))


FEDEX = events.FEDEX_SENDER


def fedex_html(rows_html, status="Out for delivery"):
    return html_only("<html><body><table><tr><td>Online FedEx Tracking</td></tr><tr><td>Ship date</td></tr><tr><td>10/20/2026</td></tr><tr><td><img alt=\"Delivery progress bar\"></td></tr>"
                     f"<tr><td>{status}</td></tr></table><table><tr><td>Tracking results as of Oct 21, 2026 4:42p GMT</td></tr><tr><td>Date/Time</td><td>Activity/Location</td></tr>{rows_html}</table>"
                     "<p>Standard transit is the date the package is scheduled to be delivered by</p></body></html>")


class FedexTests(unittest.TestCase):
    NUMBER = "123456789012"

    def read(self, rows_html, subject=None, sender=FEDEX, at="2026-10-21T16:42:35+00:00"):
        body = gmail_mod._find_text(fedex_html(rows_html), True)
        return events.extract({"id": "f1", "sender": sender, "subject": subject or f"Online FedEx Tracking - {self.NUMBER}", "received_at": at, "body_text": body})

    def test_the_newest_activity_row_is_the_state_and_only_a_delivered_row_delivers(self):
        got = self.read("<tr><td>10/21/2026 7:54 am</td><td>On FedEx vehicle for delivery</td></tr><tr><td></td><td>CITY, ST</td></tr><tr><td>10/21/2026 7:26 am</td><td>At local FedEx facility</td></tr>")
        self.assertEqual((got["kind"], got["number"], got["state"], got["delivered"]), ("SHIPMENT", self.NUMBER, "On FedEx vehicle for delivery", False))
        done = self.read("<tr><td>10/21/2026 11:02 am</td><td>Delivered</td></tr><tr><td>10/21/2026 7:54 am</td><td>On FedEx vehicle for delivery</td></tr>")
        self.assertEqual((done["state"], done["delivered"]), ("Delivered", True))
        self.assertEqual(self.read("<tr><td>10/21/2026 7:54 am</td><td>x</td></tr>", subject="Your FedEx package")["kind"], "IGNORED")
        self.assertEqual(self.read("<tr><td>nothing</td></tr>")["reason"], "SHIPMENT_STATE_MISSING")
        self.assertEqual(self.read("<tr><td>10/21/2026 7:54 am</td><td>x</td></tr>", sender=f"someone{AT}fedex-lookalike.invalid")["kind"], "IGNORED")

    def test_a_followed_shipment_updates_its_piece_and_delivery_removes_it_from_the_list_for_good(self):
        st = ItemLevelTests().full()
        st, _ = state.record_tracking(st, FORWARDED, {"carrier": "FedEx", "number": self.NUMBER}, "operator", NOW)
        way = self.read("<tr><td>10/21/2026 7:54 am</td><td>On FedEx vehicle for delivery</td></tr>")
        st, _ = state.apply(st, [way], NOW)
        text = [report_region.plain(b) for b in card.render(st, datetime(2026, 10, 21, 17, 0, tzinfo=timezone.utc))[0]]
        self.assertIn(f"#{FORWARDED}  ·  forwarded Oct 20  ·  FedEx {self.NUMBER}  ·  On FedEx vehicle for delivery", text)
        done = {**self.read("<tr><td>10/21/2026 11:02 am</td><td>Delivered</td></tr>", at="2026-10-21T16:02:00+00:00"), "id": "f2"}
        late = {**way, "id": "f3", "at": "2026-10-21T16:30:00+00:00"}                                       # a stale "on the way" copy that arrives after delivery
        st, _ = state.apply(st, [done, late], NOW)
        text = [report_region.plain(b) for b in card.render(st, datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc))[0]]
        self.assertFalse(any(t.startswith(f"#{FORWARDED}") for t in text))
        self.assertEqual(text[-1], "Last 7 days: 10 recycled, 1 shredded, 1 delivered")
        again, _ = state.apply(st, [done, late], NOW)
        self.assertEqual(again, st)                                                                          # idempotent
        other, _ = state.apply(ItemLevelTests().full(), [way], NOW)                                          # an update for a number no piece holds changes no piece
        self.assertTrue(any(t.startswith(f"#{FORWARDED}") and "Tracking unavailable" in t for t in [report_region.plain(b) for b in card.render(other, datetime(2026, 10, 21, 17, 0, tzinfo=timezone.utc))[0]]))


if __name__ == "__main__":
    unittest.main()
