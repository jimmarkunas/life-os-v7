"""Phase 4 (synthetic): an action started in Notion is tied to the right person, workspace, record and grant, with no reusable backend token in a page. Events are fabricated; no Notion call is made."""
import hashlib
import hmac
import json
import unittest

from lifeos.inbox import inbox, notion_binding as N
from lifeos.inbox.actions import ActionService
from lifeos.inbox.model import InboxError
from tests.inbox import fixtures as F

TOKEN = b"synthetic-verification-token"
JIM_NOTION = "notion-user-jim"
ROW = "row-page-1"


def event(**over):
    base = {"id": "evt-1", "type": N.EVENT_TYPE, "workspace_id": "ws-1", "authors": [{"id": JIM_NOTION, "type": "person"}], "entity": {"id": ROW, "type": "page"}, "data": {}}
    return {**base, **over}


def signed(body, token=TOKEN):
    raw = json.dumps(body, sort_keys=True).encode()
    return raw, "sha256=" + hmac.new(token, raw, hashlib.sha256).hexdigest()


class Binding(unittest.TestCase):
    def setUp(self):
        self.who = F.principal()
        self.result = inbox.build_inbox(F.all_sources(), self.who, F.NOW)
        self.record = F.record_for(self.result, "jira", "personal")
        self.rows = {ROW: {"record_id": self.record.record_id, "requested_action": "accept_uat"}}
        self.map = {ROW: self.record.record_id}
        self.seen = set()

    def intent(self, body=None, header=None, raw=None, rows=None, **kw):
        raw_body, good = signed(body if body is not None else event())
        args = dict(notion_workspace_id="ws-1", principals={JIM_NOTION: "jim"}, row_map=self.map, read_row=lambda page: (rows or self.rows)[page], seen_events=self.seen)
        return N.intent_from_event(TOKEN, raw if raw is not None else raw_body, header if header is not None else good, **{**args, **kw})

    def test_a_valid_signed_event_yields_the_person_the_row_and_the_requested_action(self):
        got = self.intent()
        self.assertEqual(got, N.Intent("jim", self.record.record_id, "accept_uat", ROW, "evt-1"))

    def test_the_event_alone_decides_only_who_where_and_which_row(self):
        for lie in ({"data": {"requested_action": "reject_uat", "record_id": "jira:bytalos:x:y"}}, {"workspace": "bytalos"}):
            self.seen.clear()
            self.assertEqual(self.intent(event(**lie)).record_id, self.record.record_id)                     # what the payload claims about action or record is ignored
        self.assertEqual(self.intent(event(id="evt-2")).action, "accept_uat")

    def test_forged_unsigned_tampered_and_replayed_events_are_refused(self):
        raw, header = signed(event())
        for label, kw, reason in (("wrong token", {"header": "sha256=" + hmac.new(b"other", raw, hashlib.sha256).hexdigest()}, "signature_invalid"),
                                  ("no header", {"header": ""}, "signature_invalid"),
                                  ("edited body", {"raw": raw.replace(b"evt-1", b"evt-9")}, "signature_invalid")):
            with self.assertRaises(InboxError, msg=label) as raised:
                self.intent(**kw)
            self.assertEqual(raised.exception.reason, reason, label)
        self.intent()
        with self.assertRaises(InboxError) as raised:
            self.intent()
        self.assertEqual(raised.exception.reason, "event_replayed")

    def test_the_author_must_be_one_allowlisted_person(self):
        for label, authors, reason in (("a bot", [{"id": JIM_NOTION, "type": "bot"}], "author_not_a_person"), ("an agent", [{"id": JIM_NOTION, "type": "agent"}], "author_not_a_person"),
                                       ("two authors", [{"id": JIM_NOTION, "type": "person"}, {"id": "x", "type": "person"}], "author_not_a_person"),
                                       ("a stranger", [{"id": "notion-user-eve", "type": "person"}], "author_not_allowlisted"), ("nobody", [], "author_not_a_person")):
            with self.assertRaises(InboxError, msg=label) as raised:
                self.intent(event(authors=authors))
            self.assertEqual(raised.exception.reason, reason, label)

    def test_the_row_workspace_and_event_type_must_match_what_oracle_wrote(self):
        for label, body, rows, reason in (("other notion workspace", event(workspace_id="ws-evil"), None, "notion_workspace_mismatch"),
                                          ("other event type", event(type="page.content_updated"), None, "event_type_unsupported"),
                                          ("a row we never wrote", event(entity={"id": "someone-elses", "type": "page"}), None, "row_unknown"),
                                          ("id column edited", event(), {ROW: {"record_id": "jira:bytalos:jim-bytalos:LIFE-2", "requested_action": "accept_uat"}}, "row_tampered"),
                                          ("not an action", event(), {ROW: {"record_id": self.record.record_id, "requested_action": "edit"}}, "no_valid_request"),
                                          ("a read is not a request", event(), {ROW: {"record_id": self.record.record_id, "requested_action": "open"}}, "no_valid_request")):
            with self.assertRaises(InboxError, msg=label) as raised:
                self.intent(body, rows=rows)
            self.assertEqual(raised.exception.reason, reason, label)

    def test_a_protected_action_runs_only_after_the_same_person_confirms_a_bound_challenge_and_then_exactly_once(self):
        intent = self.intent()
        code = N.challenge_code(F.KEY, intent, self.record.revision, "nonce-1")
        for label, kw in (("wrong code", {"typed_code": "ZZZZZZZZ"}), ("another person", {"confirmer_principal_id": "mallory"}),
                          ("code for another revision", {"typed_code": N.challenge_code(F.KEY, intent, "old-revision", "nonce-1")}),
                          ("code for another nonce", {"typed_code": N.challenge_code(F.KEY, intent, self.record.revision, "nonce-2")})):
            args = dict(typed_code=code, confirmer_principal_id="jim")
            with self.assertRaises(InboxError, msg=label):
                N.authorization_from_confirmation(F.KEY, self.who, self.record, intent, "nonce-1", now=F.NOW, **{**args, **kw})
        auth = N.authorization_from_confirmation(F.KEY, self.who, self.record, intent, "nonce-1", code.lower(), "jim", F.NOW)
        executor = F.Executor()
        svc = ActionService(F.KEY, executor)
        self.assertEqual(svc.request(self.who, self.record, intent.action, auth, intent.event_id, F.NOW).status, "completed")
        self.assertEqual(svc.request(self.who, self.record, intent.action, auth, "evt-retry", F.NOW).reason, "authorization_used")
        self.assertEqual(len(executor.calls), 1)

    def test_a_valid_event_still_cannot_reach_a_workspace_or_source_the_principal_has_no_grant_for(self):
        bytalos = F.record_for(self.result, "jira", "bytalos")
        narrow = F.principal(workspaces=("personal",))
        rows = {"row-b": {"record_id": bytalos.record_id, "requested_action": "accept_uat"}}
        intent = self.intent(event(entity={"id": "row-b", "type": "page"}), rows=rows, row_map={"row-b": bytalos.record_id}, read_row=lambda page: rows[page])
        self.assertEqual(intent.record_id, bytalos.record_id)
        with self.assertRaises(InboxError) as raised:
            N.authorization_from_confirmation(F.KEY, narrow, bytalos, intent, "n", N.challenge_code(F.KEY, intent, bytalos.revision, "n"), "jim", F.NOW)
        self.assertEqual(raised.exception.reason, "no_grant")

    def test_the_notion_button_webhook_is_not_a_trust_path(self):
        self.assertEqual(N.EVENT_TYPE, "page.properties_updated")
        self.assertFalse(hasattr(N, "intent_from_button_webhook"))
        raw = json.dumps({"id": "btn-1", "pageId": ROW}).encode()
        with self.assertRaises(InboxError):                                                                 # an unsigned button POST carries no signature header and is refused
            N.intent_from_event(TOKEN, raw, "", notion_workspace_id="ws-1", principals={JIM_NOTION: "jim"}, row_map=self.map, read_row=lambda page: self.rows[page], seen_events=set())


if __name__ == "__main__":
    unittest.main()
