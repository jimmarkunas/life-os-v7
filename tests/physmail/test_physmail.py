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
from lifeos.platform.gmail import GmailError
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

    def message_record(self, message_id):
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
        self.assertEqual(gmail.queries, [(f"from:{events.DOMAIN} after:{after}", 1000)])
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
        self.assertEqual(again.queries[0][0], f"from:{events.DOMAIN} after:{int(floor.timestamp())}")

    def test_an_incomplete_listing_or_an_unreadable_message_fails_closed_and_changes_nothing(self):
        self.run_stage([mail("n1", "New Mail (3)")])
        before = copy.deepcopy(self.store.row)
        for gmail in (FakeGmail([mail("n2", "New Mail (1)")], list_error="GMAIL_LIST_LIMIT_EXCEEDED"), FakeGmail([mail("n2", "New Mail (1)")], read_error="n2")):
            with self.assertRaises(stage.PhysMailError):
                self.run_stage(env={}, gmail=gmail)
        self.assertEqual(self.store.row, before)

    def test_a_new_arrival_notice_pushes_to_the_phone_once_with_counts_only(self):
        pushed = []
        with mock.patch("lifeos.platform.alerts.ntfy", lambda topic, title, body, severity, post=None: (pushed.append((topic, title, body)) or True)):
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
        self.assertIn("1 forwarded, waiting for tracking", text)
        self.assertEqual(blocks[-1]["paragraph"]["rich_text"][0]["text"]["link"]["url"], f"https://app.{events.DOMAIN}/in")
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
        self.assertNotIn("mail-status-old", [k["id"] for k in kids])
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
        for op in ("APPEND", "DELETE"):
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


if __name__ == "__main__":
    unittest.main()
