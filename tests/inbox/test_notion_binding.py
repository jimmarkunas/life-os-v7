"""Phase 4 and LIFE-697 (synthetic): an action started in Notion is tied to the right person, workspace, record revision and grant by two signed events and an out-of-band code. Events are fabricated."""
import hashlib
import hmac
import inspect
import json
import os
import random
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta

from lifeos.inbox import inbox, notion_binding as N, policy
from lifeos.inbox.actions import ActionService
from lifeos.inbox.model import InboxError
from tests.inbox import fixtures as F

TOKEN = b"synthetic-verification-token"
JIM_NOTION, MALLORY_NOTION, BOT = "notion-user-jim", "notion-user-mallory", "notion-bot-1"
ROW = "row-page-1"
START = F.NOW


def payload(event_id, minutes, props, author=JIM_NOTION, kind="person", workspace="ws-1", row=ROW, event_type=N.EVENT_TYPE):
    return {"id": event_id, "type": event_type, "timestamp": (START + timedelta(minutes=minutes)).isoformat(), "workspace_id": workspace,
            "authors": [{"id": author, "type": kind}], "entity": {"id": row, "type": "page"}, "data": {"updated_properties": props}}


def signed(body, token=TOKEN):
    raw = json.dumps(body, sort_keys=True).encode()
    return raw, "sha256=" + hmac.new(token, raw, hashlib.sha256).hexdigest()


class Flow(unittest.TestCase):
    """One Jira Human UAT record in Personal, one Notion row for it, a durable ledger on a file, and an out-of-band code mailbox."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "ledger.db")
        self.ledger = F.file_ledger(self.path)
        self.who = F.principal()
        self.record = F.record_for(inbox.build_inbox(F.all_sources(), self.who, F.NOW), "jira", "personal")
        self.records = {self.record.record_id: self.record}
        self.row = {"record_id": self.record.record_id, "principal_id": "jim", "requested_action": "accept_uat", "confirmation_code": ""}
        self.rows = {ROW: self.row}
        self.principals = {"jim": self.who, "mallory": F.principal(sources=("calendar",))}
        self.mailbox = {}
        self.now = F.NOW
        self.rng = random.Random(7)

    def parse(self, body, token=TOKEN, **kw):
        raw, header = signed(body)
        return N.parse_event(token, raw, header, notion_workspace_id="ws-1", principals={JIM_NOTION: "jim", MALLORY_NOTION: "mallory"}, **kw)

    def common(self):
        return dict(key=F.KEY, row_map={ROW: self.record.record_id}, read_row=lambda page: self.rows.get(page), read_record=lambda rid: self.records.get(rid),
                    principal_of=lambda pid: self.principals[pid])

    def request(self, event_id="req-1", minutes=0, ledger=None, **kw):
        event = self.parse(payload(event_id, minutes, [N.PROP_ACTION], **kw))
        return N.open_intent(ledger or self.ledger, event, deliver=lambda pid, iid, code: self.mailbox.update({iid: (pid, code)}), now=self.now, rng=self.rng, **self.common())

    def confirm(self, event_id="conf-1", minutes=1, code=None, intent=None, ledger=None, now=None, **kw):
        if code is not None:
            self.row["confirmation_code"] = code
        elif intent is not None:
            self.row["confirmation_code"] = self.mailbox[intent.intent_id][1]
        event = self.parse(payload(event_id, minutes, [N.PROP_CODE], **kw))
        return N.confirm(ledger or self.ledger, event, now=now or self.now + timedelta(minutes=minutes), **self.common())

    def refused(self, call, reason):
        with self.assertRaises(InboxError) as raised:
            call()
        self.assertEqual(raised.exception.reason, reason)


class HappyPath(Flow):
    def test_two_signed_events_and_the_out_of_band_code_mint_one_exact_authorization(self):
        pending = self.request()
        self.assertEqual((pending.principal_id, pending.action), ("jim", "accept_uat"))
        self.assertEqual(self.mailbox[pending.intent_id][0], "jim")                                          # the code went to that person, not onto the row
        self.assertNotIn(self.mailbox[pending.intent_id][1], json.dumps(self.row))
        auth = self.confirm(intent=pending)
        self.assertEqual((auth.principal_id, auth.source, auth.workspace, auth.account, auth.record_id, auth.action, auth.revision),
                         ("jim", "jira", "personal", "jim-personal", self.record.record_id, "accept_uat", self.record.revision))
        service = ActionService(F.KEY, F.Executor(), self.ledger, synthetic=True)
        self.assertEqual(service.request(self.who, self.record, "accept_uat", auth, "k1", self.now).status, "completed")

    def test_the_payload_decides_only_who_and_which_row_never_the_action_or_the_record(self):
        body = payload("req-1", 0, [N.PROP_ACTION])
        body["data"].update(requested_action="reject_uat", record_id="jira:bytalos:x:y")
        pending = N.open_intent(self.ledger, self.parse(body), deliver=lambda *a: None, now=self.now, rng=self.rng, **self.common())
        self.assertEqual(pending.action, "accept_uat")                                                        # what the row says now, read fresh

    def test_confirmation_takes_no_person_from_the_caller(self):
        self.assertNotIn("confirmer_principal_id", inspect.signature(N.confirm).parameters)
        self.assertEqual([p for p in inspect.signature(N.confirm).parameters if "principal" in p and p != "principal_of"], [])


class ForgedOrMisattributed(Flow):
    def test_unsigned_forged_edited_bot_and_unlisted_events_are_refused_before_anything_is_read(self):
        raw, header = signed(payload("e1", 0, [N.PROP_ACTION]))
        for _label, call, reason in (("wrong token", lambda: N.parse_event(b"other", raw, header, notion_workspace_id="ws-1", principals={JIM_NOTION: "jim"}), "signature_invalid"),
                                    ("no header", lambda: N.parse_event(TOKEN, raw, "", notion_workspace_id="ws-1", principals={JIM_NOTION: "jim"}), "signature_invalid"),
                                    ("edited body", lambda: N.parse_event(TOKEN, raw.replace(b"e1", b"e9"), header, notion_workspace_id="ws-1", principals={JIM_NOTION: "jim"}), "signature_invalid"),
                                    ("bot", lambda: self.parse(payload("e2", 0, [N.PROP_ACTION], author=BOT, kind="bot")), "author_not_a_person"),
                                    ("not listed", lambda: self.parse(payload("e3", 0, [N.PROP_ACTION], author="notion-user-stranger")), "author_not_allowlisted"),
                                    ("other workspace", lambda: self.parse(payload("e4", 0, [N.PROP_ACTION], workspace="ws-other")), "notion_workspace_mismatch"),
                                    ("other type", lambda: self.parse(payload("e5", 0, [N.PROP_ACTION], event_type="page.created")), "event_type_unsupported")):
            self.refused(call, reason)
        body = payload("e6", 0, [N.PROP_ACTION])
        body["authors"].append({"id": MALLORY_NOTION, "type": "person"})
        self.refused(lambda: self.parse(body), "author_not_a_person")                                         # several authors: no single person to hold to
        for broken in (b"not json", json.dumps({"id": "x"}).encode(), json.dumps({**payload("e7", 0, ["a"]), "timestamp": "2026-11-14T14:00:00"}).encode()):
            self.refused(lambda broken=broken: N.parse_event(TOKEN, broken, "sha256=" + hmac.new(TOKEN, broken, hashlib.sha256).hexdigest(), notion_workspace_id="ws-1", principals={}), "event_malformed")

    def test_a_different_person_confirming_voids_the_request_even_with_the_right_code(self):                  # check 6
        pending = self.request()
        self.refused(lambda: self.confirm(intent=pending, author=MALLORY_NOTION), "confirmer_mismatch")
        self.refused(lambda: self.confirm("conf-2", intent=pending), "no_pending_request")                      # the real person must ask again


class Challenge(Flow):
    def test_wrong_codes_spend_three_tries_then_even_the_right_code_is_locked(self):                           # check 7
        pending = self.request()
        for n in range(3):
            self.refused(lambda n=n: self.confirm(f"c{n}", 1 + n, code="WRONGCODE9"), "challenge_wrong")
        self.refused(lambda: self.confirm("c9", 4, intent=pending), "challenge_locked")
        self.refused(lambda: self.confirm("c10", 4, intent=pending), "no_pending_request")

    def test_an_expired_challenge_fails_closed(self):
        pending = self.request()
        self.refused(lambda: self.confirm(intent=pending, now=self.now + N.CODE_TTL + timedelta(seconds=1)), "challenge_expired")
        self.refused(lambda: self.confirm("conf-2", 2, intent=pending), "no_pending_request")

    def test_a_used_code_cannot_be_used_again_and_a_replayed_delivery_is_refused(self):
        pending = self.request()
        self.confirm(intent=pending)
        self.refused(lambda: self.confirm("conf-2", 2, intent=pending), "no_pending_request")                   # the code was spent
        self.refused(lambda: self.confirm("conf-1", 1, intent=pending), "event_replayed")                       # the same delivery again (Notion retries up to 8 times)
        self.refused(lambda: self.request("req-1"), "event_replayed")

    def test_a_new_request_supersedes_the_old_one_so_an_old_code_never_authorizes_the_new_action(self):
        first = self.request("req-1", 0)
        self.row["requested_action"] = "reject_uat"
        second = self.request("req-2", 1)
        self.assertNotEqual(self.mailbox[first.intent_id][1], self.mailbox[second.intent_id][1])
        self.refused(lambda: self.confirm("conf-1", 2, code=self.mailbox[first.intent_id][1]), "challenge_wrong")
        auth = self.confirm("conf-2", 3, intent=second)
        self.assertEqual(auth.action, "reject_uat")


class EventOrderAndEditedRows(Flow):
    def test_out_of_order_and_coalesced_events_change_nothing(self):                                           # check 8
        pending = self.request("req-1", 3)
        self.refused(lambda: self.confirm("old", 1, intent=pending), "event_out_of_order")                       # delivered late: older than the request it answers
        both = self.parse(payload("both", 4, [N.PROP_ACTION, N.PROP_CODE]))
        self.refused(lambda: N.confirm(self.ledger, both, now=self.now, **self.common()), "event_coalesced")      # one aggregated event that changed both cannot be ordered
        self.refused(lambda: N.open_intent(self.ledger, both, deliver=lambda *a: None, now=self.now, **self.common()), "event_coalesced")
        other = self.parse(payload("other", 4, ["notes"]))
        self.refused(lambda: N.confirm(self.ledger, other, now=self.now, **self.common()), "event_not_a_confirmation")
        self.refused(lambda: N.open_intent(self.ledger, other, deliver=lambda *a: None, now=self.now, **self.common()), "event_not_a_request")
        self.assertEqual(self.confirm("ok", 4, intent=pending).action, "accept_uat")                              # the pending request itself was untouched

    def test_an_action_edited_after_the_request_never_inherits_the_old_confirmation(self):
        pending = self.request()
        self.row["requested_action"] = "reject_uat"
        self.refused(lambda: self.confirm(intent=pending), "intent_changed")
        self.row["requested_action"] = "accept_uat"
        self.refused(lambda: self.confirm("conf-2", 2, intent=pending), "no_pending_request")                   # changing it back does not revive it

    def test_a_record_that_changed_a_deleted_row_and_a_reassigned_row_void_the_request(self):
        for _label, change, reason in (("stale revision", lambda: self.records.update({self.record.record_id: replace(self.record, revision="r9")}), "record_changed"),
                                      ("deleted row", lambda: self.rows.pop(ROW), "row_deleted"),
                                      ("reassigned row", lambda: self.row.update(principal_id="mallory"), "row_reassigned"),
                                      ("edited id column", lambda: self.row.update(record_id="jira:bytalos:x:y"), "row_tampered"),
                                      ("record gone", lambda: self.records.clear(), "record_unavailable")):
            self.setUp()
            pending = self.request()
            code = self.mailbox[pending.intent_id][1]
            change()
            self.row["confirmation_code"] = code
            self.refused(lambda code=code: self.confirm(code=code), reason)

    def test_access_revoked_between_the_request_and_the_confirmation_refuses(self):
        pending = self.request()
        self.principals["jim"] = F.principal(revoked={("jira", "personal")})
        self.refused(lambda: self.confirm(intent=pending), "grant_revoked")

    def test_a_process_restart_between_request_and_confirmation_keeps_the_pending_request_and_every_refusal(self):
        pending = self.request()
        restarted = F.file_ledger(self.path, create=False)                                                      # a new process, the same durable state
        self.refused(lambda: self.request("req-1", ledger=restarted), "event_replayed")
        auth = self.confirm(intent=pending, ledger=restarted)
        self.assertEqual(auth.record_id, self.record.record_id)
        self.refused(lambda: self.confirm("conf-2", 2, intent=pending, ledger=F.file_ledger(self.path, create=False)), "no_pending_request")

    def test_a_request_the_policy_would_refuse_never_creates_a_pending_confirmation(self):
        self.row["requested_action"] = "edit"
        self.refused(lambda: self.request(), "no_valid_request")
        self.setUp()
        self.principals["jim"] = F.principal(sources=("calendar",))
        self.refused(lambda: self.request(), "no_grant")
        self.assertIsNone(self.ledger.pending_intent(ROW))


class TheMintedAuthorizationStillMeetsEveryGate(Flow):
    def test_a_confirmed_authorization_cannot_cross_workspace_source_or_a_new_revision(self):
        auth = self.confirm(intent=self.request())
        service = ActionService(F.KEY, F.Executor(), self.ledger, synthetic=True)
        other = F.record_for(inbox.build_inbox(F.all_sources(), self.who, F.NOW), "jira", "bytalos")
        self.assertEqual(service.request(self.who, other, "accept_uat", auth, "k1", self.now).reason, "authorization_mismatch")
        newer = replace(self.record, revision="r9")
        self.assertEqual(service.request(self.who, newer, "accept_uat", auth, "k2", self.now).reason, "authorization_mismatch")
        self.assertEqual(service.request(F.principal(workspaces=("bytalos",)), self.record, "accept_uat", auth, "k3", self.now).reason, "no_grant")
        self.assertEqual(policy.decide(self.who, replace(other, workspace="tgp"), "accept_uat", F.NOW), (False, "workspace_not_released"))


if __name__ == "__main__":
    unittest.main()
