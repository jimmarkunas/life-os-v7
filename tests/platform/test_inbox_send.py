"""(synthetic): the governed send_to_jim operation, with the Notion double of the inbox tests."""
import unittest
from datetime import datetime, timezone

from lifeos.platform import inbox_send as S
from tests.inbox.test_agent_inbox import FakeClient

NOW = datetime(2026, 11, 23, 5, 0, tzinfo=timezone.utc)


def send(client, **args):
    return S.run("send_to_jim", {"subject": "Synthetic LIFE-876 test item", "_client": "mac-claude-code", **args}, {"client": client, "now": lambda: NOW})


class SendToJim(unittest.TestCase):
    def test_an_item_is_created_with_the_verified_client_as_sender_and_a_repeat_updates_the_same_row(self):
        client = FakeClient()
        first = send(client)
        self.assertEqual((first["created"], first["sender"], first["workspace"]), (1, "mac-claude-code", "Personal"))
        self.assertEqual(len(client.rows), 1)
        again = send(client, details="a later note")
        self.assertEqual(("updated" in again, "created" in again, len(client.rows)), (True, False, 1))

    def test_the_sender_is_only_ever_the_server_supplied_client_never_a_field_the_caller_sent(self):
        client = FakeClient()
        out = send(client, **{"from": "claude-chat"})  # a forged From in the arguments is ignored
        self.assertEqual(out["sender"], "mac-claude-code")
        self.assertNotIn("from", S.OPS["send_to_jim"][1])  # and the schema offers no such field

    def test_another_clients_key_is_refused_and_changes_nothing(self):
        client = FakeClient()
        send(client, key="shared-key-1")
        with self.assertRaises(S.InboxSendError) as raised:
            send(client, key="shared-key-1", **{"_client": "oracle-claude-code"})
        self.assertEqual((raised.exception.reason, len(client.rows)), ("key_other_sender", 1))

    def test_the_gate_route_is_the_items_workspace_a_write_on_agent_inbox_and_an_unknown_workspace_is_an_unknown_tenant(self):
        self.assertEqual(S.route("send_to_jim", {}, "x"), ("personal", "write", "agent-inbox", "notion"))
        self.assertEqual(S.route("send_to_jim", {"workspace": "Bytalos"}, "x")[0], "bytalos")
        for name in ("TGP", "Other", "personal"):
            self.assertEqual(S.route("send_to_jim", {"workspace": name}, "x")[0], "unknown", name)

    def test_without_a_notion_client_and_for_a_bad_item_the_reasons_are_fixed(self):
        with self.assertRaises(S.InboxSendError) as raised:
            S.run("send_to_jim", {"subject": "x", "_client": "c"}, {"client": None})
        self.assertEqual(raised.exception.reason, "inbox_not_configured")
        with self.assertRaises(S.InboxSendError) as raised:
            send(FakeClient(), urgency="Whenever")
        self.assertEqual(raised.exception.reason, "urgency_invalid")

    def test_wire_needs_the_read_write_integrations_token_by_that_one_name(self):
        state = {}
        S.wire({"NOTION_API_TOKEN": "the-hourly-write-token-123"}, state)  # the pipeline's token is never used
        self.assertIsNone(state["client"])
        S.wire({"NOTION_READONLY_TOKEN": "tok-synthetic-12345"}, state)
        self.assertEqual(type(state["client"]).__name__, "Client")


if __name__ == "__main__":
    unittest.main()
