"""LIFE-791: the Agent Inbox: what a writer may add, where it may write, and the tile recount. A fake Notion client: no network, no token."""
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from lifeos.inbox import agent_inbox, agent_inbox_stage, send_to_jim
from lifeos.inbox.model import InboxError

NOW = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)
SOURCE = "a1" * 16
PAGE = "b2" * 16


def ok_item(**over):
    return {"from": "claude-code", "subject": "Approve the key rotation plan", "urgency": "Today", "workspace": "Personal", **over}


class FakeClient:
    """A Notion double that keeps rows, answers the four calls the inbox makes, and remembers every write."""

    def __init__(self, found=1, tiles=True, source=SOURCE, parent_is_page=False):
        self.source, self.rows, self.writes, self.blocks, self.calls_made = source, [], [], {}, []
        self.found = found
        self.tiles = tiles
        self.parent_is_page = parent_is_page                    # LIFE-891: the database sits inside the Agent Inbox page
        self._build_page()

    def _build_page(self):
        self.page_children = [{"id": "cl", "type": "column_list"}]
        self.blocks["cl"] = [{"id": f"col{i}", "type": "column"} for i in range(5)]
        for i in range(5):
            self.blocks[f"col{i}"] = [{"id": f"co{i}", "type": "callout"}]
            self.blocks[f"co{i}"] = [{"id": f"h{i}", "type": "heading_3", "heading_3": {"rich_text": [{"plain_text": "-"}]}},
                                     {"id": f"p{i}", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "old"}]}}]

    def call(self, method, path, body=None):
        self.calls_made.append((method, path))
        if path == "/search":
            if body["filter"]["value"] == "data_source":
                return {"results": [{"object": "data_source", "id": SOURCE, "title": [{"plain_text": agent_inbox.DB_TITLE}]} for _ in range(self.found)], "has_more": False}
            return {"results": [{"object": "page", "id": PAGE, "properties": {"title": {"title": [{"plain_text": agent_inbox.PAGE_TITLE}]}}}] if self.tiles else [], "has_more": False}
        if method == "GET" and path.startswith("/data_sources/"):
            return {"parent": {"type": "database_id", "database_id": "db"}}
        if method == "GET" and path.startswith("/databases/"):
            return {"parent": {"type": "page_id", "page_id": PAGE} if self.parent_is_page else {"type": "workspace"}}
        if method == "GET" and path == f"/pages/{PAGE}":
            return {"id": PAGE, "properties": {"title": {"title": [{"plain_text": agent_inbox.PAGE_TITLE}]}}}
        if method == "GET" and path.startswith(f"/blocks/{PAGE}/children"):
            return {"results": self.page_children, "has_more": False}
        if method == "GET" and path.startswith("/blocks/"):
            return {"results": self.blocks[path.split("/")[2].split("?")[0]], "has_more": False}
        raise AssertionError(f"unexpected call {method} {path}")

    def call_once(self, method, path, body=None):
        self.writes.append((method, path, body))
        if method == "POST" and path == "/pages":
            props = body["properties"]
            self.rows.append({"id": f"row{len(self.rows)}", "status": props["Status"]["select"]["name"], "urgency": props["Urgency"]["select"]["name"], "key": props["Key"]["rich_text"][0]["text"]["content"],
                              "from": props["From"]["rich_text"][0]["text"]["content"], "created": NOW})
        elif method == "PATCH" and body.get("in_trash"):
            self.rows = [r for r in self.rows if f"/pages/{r['id']}" != path]
        return {}

    def update_block(self, block_id, kind, rich):
        self.writes.append(("PATCH", f"/blocks/{block_id}", {kind: rich}))
        for block in self.blocks.values():
            for item in block:
                if item["id"] == block_id:
                    item[kind] = {"rich_text": [{"plain_text": rich[0]["text"]["content"]}]}

    def query_data_source(self, source, body):
        assert source == SOURCE, "a query reached a data source that is not the inbox"
        flt = body["filter"]
        parts = flt.get("and", [flt])
        rows = self.rows
        for part in parts:
            if part.get("property") == "Key":
                rows = [r for r in rows if r["key"] == part["rich_text"]["equals"]]
            elif part.get("property") == "From":
                rows = [r for r in rows if r["from"] == part["rich_text"]["equals"]]
            elif part.get("property") == "Status":
                rows = [r for r in rows if r["status"] == part["select"]["equals"]]
            elif part.get("property") == "Urgency":
                rows = [r for r in rows if r["urgency"] == part["select"]["equals"]]
            elif part.get("timestamp") == "created_time":
                after = datetime.fromisoformat(part["created_time"]["after"])
                rows = [r for r in rows if r["created"] > after]
        return {"results": rows[:body.get("page_size", 100)], "has_more": False}


class Validation(unittest.TestCase):
    def test_a_good_item_normalizes_with_a_stable_key(self):
        a, b = agent_inbox.normalize(ok_item()), agent_inbox.normalize(ok_item())
        self.assertEqual(a["key"], b["key"])
        self.assertEqual((a["urgency"], a["workspace"], a["due"], a["link"]), ("Today", "Personal", "", ""))

    def test_defaults_are_today_and_personal(self):
        item = agent_inbox.normalize({"from": "codex", "subject": "x"})
        self.assertEqual((item["urgency"], item["workspace"]), ("Today", "Personal"))

    def test_every_refusal_is_a_fixed_word(self):
        cases = {"from_missing": ok_item(**{"from": ""}), "subject_missing": ok_item(subject=" "), "urgency_invalid": ok_item(urgency="Soon"), "workspace_invalid": ok_item(workspace="TGP"),
                 "subject_invalid": ok_item(subject="x" * (agent_inbox.SUBJECT_MAX + 1)), "due_invalid": ok_item(due="tomorrow-ish"), "link_invalid": ok_item(link="ftp://host/x"),
                 "from_invalid": ok_item(**{"from": "sender name with spaces and an at sign @"}), "key_invalid": ok_item(key="bad key!"), "details_invalid": ok_item(details="y" * 1501)}
        for word, item in cases.items():
            with self.subTest(word):
                with self.assertRaises(InboxError) as caught:
                    agent_inbox.normalize(item)
                self.assertEqual(caught.exception.reason, word)

    def test_secrets_and_personal_data_never_pass(self):
        fakes = {"nsec": "n" + "sec1" + "q" * 40, "github": "gh" + "p_" + "A" * 30, "private key": "-----BEGIN " + "RSA PRIVATE KEY-----", "bearer": "Bearer " + "x" * 30,
                 "assignment": "pass" + "word: " + "hunter2hunter2", "hex": "ab" * 25}
        for name, text in fakes.items():
            for field in ("subject", "details"):
                with self.subTest(f"{name} {field}"):
                    with self.assertRaises(InboxError) as caught:
                        agent_inbox.normalize(ok_item(**{field: f"see {text} now"}))
                    self.assertEqual(caught.exception.reason, f"{field}_has_secret")
        for text in ("mail jo" + "e@" + "corp-name.com", "ssn 123-45-" + "6789"):
            with self.assertRaises(InboxError) as caught:
                agent_inbox.normalize(ok_item(details=text))
            self.assertEqual(caught.exception.reason, "details_has_personal_data")

    def test_a_synthetic_host_link_is_refused(self):
        for host in ("example." + "test", "thing." + "invalid", "example." + "com", "localhost"):
            with self.assertRaises(InboxError) as caught:
                agent_inbox.normalize(ok_item(link=f"https://{host}/x"))
            self.assertEqual(caught.exception.reason, "link_synthetic_host")
        self.assertEqual(agent_inbox.normalize(ok_item(link="https://github.com/org/repo/pull/1"))["link"], "https://github.com/org/repo/pull/1")

    def test_properties_always_start_as_new_and_set_nothing_else(self):
        props = agent_inbox.properties(agent_inbox.normalize(ok_item(due="2026-11-20")))
        self.assertEqual(props["Status"], {"select": {"name": "New"}})
        self.assertEqual(sorted(props), ["Details", "Due", "From", "Key", "Status", "Subject", "Urgency", "Workspace"])
        for never in ("Reply", "Replied", "Snooze until", "Promoted to"):
            self.assertNotIn(never, props)


class Sending(unittest.TestCase):
    def test_send_creates_one_row_in_the_inbox_source_only(self):
        client = FakeClient()
        self.assertEqual(agent_inbox.send(client, ok_item(), now=NOW), {"created": 1})
        self.assertEqual([w[:2] for w in client.writes], [("POST", "/pages")])
        self.assertEqual(client.writes[0][2]["parent"], {"type": "data_source_id", "data_source_id": SOURCE})

    def test_the_same_key_never_adds_a_second_row(self):
        client = FakeClient()
        agent_inbox.send(client, ok_item(key="k1"), now=NOW)
        self.assertEqual(agent_inbox.send(client, ok_item(key="k1", subject="different words"), now=NOW), {"duplicate": 1})
        self.assertEqual(len(client.rows), 1)

    def test_a_sender_over_the_hourly_rate_is_refused(self):
        client = FakeClient()
        for n in range(agent_inbox.RATE_PER_HOUR):
            agent_inbox.send(client, ok_item(subject=f"item {n}"), now=NOW)
        with self.assertRaises(InboxError) as caught:
            agent_inbox.send(client, ok_item(subject="one too many"), now=NOW)
        self.assertEqual(caught.exception.reason, "rate_limited")
        agent_inbox.send(client, ok_item(**{"from": "codex"}, subject="another sender is fine"), now=NOW)
        agent_inbox.send(client, ok_item(subject="an hour later"), now=NOW + timedelta(hours=1, minutes=1))

    def test_no_database_or_several_is_a_fixed_refusal(self):
        for found, word in ((0, "inbox_not_found"), (2, "inbox_ambiguous")):
            with self.assertRaises(InboxError) as caught:
                agent_inbox.send(FakeClient(found=found), ok_item(), now=NOW)
            self.assertEqual(caught.exception.reason, word)

    def test_a_refused_item_writes_nothing(self):
        client = FakeClient()
        with self.assertRaises(InboxError):
            agent_inbox.send(client, ok_item(details="gh" + "p_" + "A" * 30), now=NOW)
        self.assertEqual(client.writes, [])


class Tiles(unittest.TestCase):
    def test_recount_writes_the_numbers_and_labels_and_is_idempotent(self):
        client = FakeClient()
        for n, urgency in enumerate(["Now", "Now", "Today", "FYI"]):
            agent_inbox.send(client, ok_item(subject=f"s{n}", urgency=urgency), now=NOW)
        client.rows[-1]["status"] = "Snoozed"
        first = agent_inbox.recount_page(client)
        self.assertEqual(first, {"tiles": 5, "tiles_changed": 10})
        numbers = [client.blocks[f"co{i}"][0]["heading_3"]["rich_text"][0]["plain_text"] for i in range(5)]
        labels = [client.blocks[f"co{i}"][1]["paragraph"]["rich_text"][0]["plain_text"] for i in range(5)]
        self.assertEqual((numbers, labels), (["2", "1", "0", "0", "1"], ["Now", "Today", "This week", "FYI", "Snoozed"]))
        self.assertEqual(agent_inbox.recount_page(client), {"tiles": 5, "tiles_changed": 0})

    def test_only_the_five_tiles_are_ever_patched(self):
        client = FakeClient()
        agent_inbox.recount_page(client)
        patched = {w[1] for w in client.writes}
        self.assertEqual(patched, {f"/blocks/{b}{i}" for b in "hp" for i in range(5)})

    def test_a_page_whose_tiles_changed_shape_is_left_alone(self):
        client = FakeClient()
        client.blocks["co0"] = [{"id": "x", "type": "paragraph", "paragraph": {"rich_text": []}}]
        with self.assertRaises(InboxError) as caught:
            agent_inbox.recount_page(client)
        self.assertEqual(caught.exception.reason, "page_not_found")
        self.assertEqual(client.writes, [])


class Archiving(unittest.TestCase):
    def test_only_the_proofs_own_test_rows_can_be_trashed(self):
        client = FakeClient()
        agent_inbox.send(client, ok_item(key="proof-1-x"), now=NOW)
        agent_inbox.send(client, ok_item(key="real-item", subject="keep me"), now=NOW)
        with self.assertRaises(InboxError) as caught:
            agent_inbox.archive_keys(client, ["real-item"])
        self.assertEqual(caught.exception.reason, "archive_not_a_test_row")
        self.assertEqual(len(client.rows), 2)
        self.assertEqual(agent_inbox.archive_keys(client, ["proof-1-x"]), 1)
        self.assertEqual([r["key"] for r in client.rows], ["real-item"])


class Tool(unittest.TestCase):
    def test_deliver_returns_counts_and_never_content(self):
        client = FakeClient()
        out = send_to_jim.deliver(ok_item(subject="a secret-looking subject that is fine"), client=client, now=NOW)
        self.assertEqual((out["ok"], out["created"]), (True, 1))
        self.assertNotIn("subject", json.dumps(out))

    def test_deliver_without_a_token_writes_nothing(self):
        self.assertEqual(send_to_jim.deliver(ok_item(), environ={}), {"ok": False, "reason": "config_missing"})

    def test_deliver_reports_a_refusal_as_a_word(self):
        self.assertEqual(send_to_jim.deliver(ok_item(urgency="Soon"), client=FakeClient(), now=NOW), {"ok": False, "reason": "urgency_invalid"})

    def test_the_row_survives_a_tile_failure(self):
        client = FakeClient(tiles=False)
        out = send_to_jim.deliver(ok_item(), client=client, now=NOW)
        self.assertEqual((out["ok"], out["created"], out["tiles_changed"]), (True, 1, -1))

    def test_mcp_lists_one_tool_and_calls_it(self):
        client = FakeClient()
        lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}), json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                 json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                 json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "send_to_jim", "arguments": ok_item()}}),
                 json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "something_else", "arguments": {}}})]
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            send_to_jim.serve(lines, client=client)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4])
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]], ["send_to_jim"])
        self.assertFalse(replies[2]["result"]["isError"])
        self.assertEqual(replies[3]["error"]["message"], "method_not_found")
        self.assertEqual(len(client.rows), 1)


class Stage(unittest.TestCase):
    def test_proof_leaves_no_row_and_dry_run_touches_nothing(self):
        client = FakeClient()
        env = {"INBOX_VIEW_MODE": "proof", "INBOX_VIEW_CONFIRM": "INBOX", "NOTION_API_TOKEN": "x"}
        self.assertEqual(agent_inbox_stage.run(0, False, environ=env, client=client, clock=lambda: NOW), {"dry_run": 1})
        self.assertEqual(client.writes, [])
        out = agent_inbox_stage.run(0, True, environ=env, client=client, clock=lambda: NOW)
        self.assertEqual((out["sent"], out["direct"], out["archived"]), (3, 1, 4))
        self.assertEqual(client.rows, [])
        self.assertEqual((out["before_now"], out["before_today"], out["before_fyi"]), (1, 2, 1))
        self.assertEqual((out["after_now"], out["after_today"], out["after_fyi"]), (0, 0, 0))

    def test_a_wrong_confirm_is_a_dry_run(self):
        client = FakeClient()
        env = {"INBOX_VIEW_MODE": "proof", "INBOX_VIEW_CONFIRM": "QA", "NOTION_API_TOKEN": "x"}
        self.assertEqual(agent_inbox_stage.run(0, True, environ=env, client=client), {"dry_run": 1})
        self.assertEqual(client.writes, [])

    def test_the_hourly_path_follows_the_switch(self):
        self.assertEqual(agent_inbox_stage.run(0, True, environ={"ACTION_INBOX_VIEW": "off"}), {"off": 1})
        client = FakeClient()
        self.assertEqual(agent_inbox_stage.run(0, True, environ={"ACTION_INBOX_VIEW": "on"}, client=client)["tiles"], 5)

    def test_the_stage_never_reads_another_system(self):
        import lifeos.inbox.agent_inbox as module
        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for word in ("jira", "todoist", "calendar", "asana", "concord", "followup"):
            self.assertNotIn(f"import {word}", source.lower())


if __name__ == "__main__":
    unittest.main()
