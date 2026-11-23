"""LIFE-865: client identity is derived from trusted facts only, every refusal reason reaches a real handler, two clients on one host cannot impersonate one another, a contract test fails if a tool handler skips the gate, and the seeded grants are what the data says."""
import ast
import io
import json
import pathlib
import re
import unittest
from datetime import datetime, timezone
from unittest import mock

from lifeos.inbox import send_to_jim
from lifeos.platform import authorization_gate as G
from lifeos.platform import mcp_access as M
from tests.inbox.test_agent_inbox import FakeClient
from tests.inbox.test_agent_inbox import ok_item as inbox_item
from tests.platform.mcp_access_fixture import CATALOG, DOC, NOW, build, open_access

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAC = {"os_user": "jimmarkunas"}


def ok_item(**over):
    """LIFE-876: send_to_jim derives From from the authenticated client row, so a valid item names that client (the Mac client here) or omits From."""
    return inbox_item(**{"from": "claude-code-mac", **over})


def call(lines, access, facts=MAC, client=None, environ=None, now=NOW):
    out, receipts, client = io.StringIO(), [], client or FakeClient()
    with mock.patch("sys.stdout", out):
        send_to_jim.serve(lines, environ=environ, client=client, access=access, facts=facts, audit=receipts.append, now=now)
    return [json.loads(x) for x in out.getvalue().splitlines()], receipts, client


def tool_call(arguments, name="send_to_jim"):
    return [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})]


def reason(replies):
    return json.loads(replies[0]["result"]["content"][0]["text"]).get("reason")


class Identity(unittest.TestCase):
    ACCESS = build()

    def test_a_trusted_fact_names_one_client_and_nothing_else_does(self):
        self.assertEqual(M.identify(self.ACCESS, "http", {"oauth_client": "claude"})["id"], "claude-chat")
        self.assertEqual(M.identify(self.ACCESS, "http", {"oauth_client": "chatgpt"})["id"], "chatgpt-chat")
        self.assertEqual(M.identify(self.ACCESS, "stdio", MAC)["id"], "claude-code-mac")
        for transport, facts in (("http", {"oauth_client": "other"}), ("http", {}), ("stdio", {"os_user": "someone"}), ("stdio", {"oauth_client": "claude"}), ("http", {"os_user": "jimmarkunas"})):
            with self.assertRaises(M.AccessError) as caught:
                M.identify(self.ACCESS, transport, facts)
            self.assertEqual(str(caught.exception), "identity_unknown")

    def test_two_clients_that_share_every_fact_are_ambiguous_and_refused(self):
        clients = [{"id": "oracle-claude-code", "transport": "stdio", "facts": {"os_user": "ubuntu"}, "policy_key": "a" * 64}, {"id": "hermes-default", "transport": "stdio", "facts": {"os_user": "ubuntu"}, "policy_key": "b" * 64}]
        access = build(clients=DOC["clients"] + clients)
        with self.assertRaises(M.AccessError) as caught:
            M.identify(access, "stdio", {"os_user": "ubuntu"})
        self.assertEqual(str(caught.exception), "identity_ambiguous")

    def test_the_principal_belongs_to_one_tenant_and_an_unknown_tenant_is_refused(self):
        client = M.identify(self.ACCESS, "http", {"oauth_client": "claude"})
        self.assertEqual((M.principal_id(client, "personal"), M.principal_id(client, "bytalos")), ("claude-chat", "claude-chat-bytalos"))
        with self.assertRaises(M.AccessError):
            M.principal_id(client, "elsewhere")

    def test_an_unreadable_or_secret_bearing_file_is_never_a_partial_answer(self):
        for text in ("not json", "{}", json.dumps({**DOC, "version": 2}), json.dumps({**DOC, "clients": [{"id": "x", "transport": "stdio", "facts": {}, "policy_key": "c" * 64}]}),
                     json.dumps({**DOC, "grants": [{**DOC["grants"][0], "credential_ref": "token=" + "A" * 24}]})):
            with self.assertRaises(M.AccessError) as caught:
                M.load(text, json.dumps(CATALOG))
            self.assertEqual(str(caught.exception), "governance_unavailable")
        with self.assertRaises(M.AccessError):
            M.load(json.dumps(DOC), "not json")


class SendToJim(unittest.TestCase):
    """The stdio server, through its real serve loop: the client is the OS user, never an argument, the sender field or the environment."""
    def test_a_granted_call_runs_and_the_receipt_names_tenant_and_purpose(self):
        replies, receipts, client = call(tool_call(ok_item()), build())
        self.assertFalse(replies[0]["result"]["isError"])
        self.assertEqual(len(client.rows), 1)
        allow = receipts[0]
        self.assertEqual((allow["outcome"], allow["tenant"], allow["tool"], allow["purpose"], allow["action"], allow["principal_fp"]), ("allow", "personal", "send_to_jim", "agent-inbox", "write", G.fingerprint("claude-code-mac")))

    def test_every_refusal_reason_stops_before_the_inbox_row(self):
        def edit(**patch):
            return build(grants=lambda seeded: [{**g, **patch} if g["tool"] == "send_to_jim" else g for g in seeded])
        cases = (("grant_revoked", edit(revoked=True), ok_item()), ("grant_expired", edit(expires="2026-11-01"), ok_item()), ("purpose_mismatch", edit(purpose="other"), ok_item()),
                 ("no_grant", build(grants=lambda seeded: [g for g in seeded if g["tool"] != "send_to_jim"]), ok_item()),
                 ("catalog_unknown_principal", build(), ok_item(workspace="Bytalos")),             # the seed registers the Mac client in Personal only: a cross-tenant call
                 ("unknown_tenant", build(), ok_item(workspace="Elsewhere")))
        for why, access, item in cases:
            replies, receipts, client = call(tool_call(item), access)
            self.assertTrue(replies[0]["result"]["isError"], why)
            self.assertEqual(reason(replies), why if why == "unknown_tenant" else "denied_" + why)
            self.assertEqual(client.rows, [], why)
            if why != "unknown_tenant":
                self.assertEqual((receipts[-1]["outcome"], receipts[-1]["reason"]), ("deny", why))

    def test_an_unregistered_os_user_and_unreadable_data_refuse_every_call(self):
        replies, receipts, client = call(tool_call(ok_item()), build(), facts={"os_user": "stranger"})
        self.assertEqual((reason(replies), client.rows, receipts), ("identity_unknown", [], []))
        with mock.patch("lifeos.platform.mcp_access.load", side_effect=M.AccessError("governance_unavailable")):
            replies, _, client = call(tool_call(ok_item()), None)
        self.assertEqual((reason(replies), client.rows), ("governance_unavailable", []))

    def test_an_unwritable_receipt_means_no_row(self):
        client = FakeClient()
        def broken(record):
            raise OSError("disk")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            send_to_jim.serve(tool_call(ok_item()), client=client, access=build(), facts=MAC, audit=broken, now=NOW)
        self.assertEqual((client.rows, reason([json.loads(out.getvalue())])), ([], "governance_unavailable"))

    def test_a_spoofed_sender_or_environment_never_changes_who_the_client_is(self):
        other = [{"id": "other-agent", "transport": "stdio", "facts": {"os_user": "someoneelse"}, "policy_key": "d" * 64}]
        access = build(clients=DOC["clients"] + other)
        item = ok_item(**{"from": "other-agent"})                                                      # the sender field is data, never identity: a different From is refused before the gate and before any row
        replies, receipts, client = call(tool_call(item), access, environ={"LIFEOS_CLIENT_ID": "other-agent", "LIFEOS_PRINCIPAL": "other-agent"})
        self.assertEqual((reason(replies), client.rows, receipts), ("from_spoofed", [], [{"tool": "send_to_jim", "principal_fp": G.fingerprint("claude-code-mac"), "outcome": "deny", "reason": "from_spoofed"}]))      # the refusal is audited under the real client
        replies, receipts, client = call(tool_call(ok_item()), access, environ={"LIFEOS_CLIENT_ID": "other-agent", "LIFEOS_PRINCIPAL": "other-agent"})
        self.assertEqual(receipts[0]["principal_fp"], G.fingerprint("claude-code-mac"))                   # the environment names another client; the gate still sees the OS user's own
        replies, receipts, client = call(tool_call(ok_item(**{"from": "other-agent"})), access, facts={"os_user": "someoneelse"}, environ={"LIFEOS_CLIENT_ID": "claude-code-mac"})
        self.assertEqual((reason(replies), client.rows), ("denied_catalog_unknown_principal", []))                # the other OS user is its own client: the Mac client's id in the environment changes nothing

    def test_two_clients_on_one_host_cannot_impersonate_one_another(self):
        """Each client has its own OS user; hermes asking as the Mac client (by id, environment or sender) is still hermes, and hermes has no grant."""
        hermes = {"id": "hermes-default", "transport": "stdio", "facts": {"os_user": "hermes"}, "policy_key": "e" * 64}
        access = build(clients=DOC["clients"] + [hermes])
        replies, receipts, client = call(tool_call(ok_item()), access, facts={"os_user": "hermes"}, environ={"LIFEOS_CLIENT_ID": "claude-code-mac"})      # hermes sends From = the Mac client: refused before the gate, no row
        self.assertEqual((reason(replies), client.rows, receipts[-1]["reason"], receipts[-1]["principal_fp"]), ("from_spoofed", [], "from_spoofed", G.fingerprint("hermes-default")))
        replies, receipts, client = call(tool_call(ok_item(**{"from": "hermes-default"})), access, facts={"os_user": "hermes"}, environ={"LIFEOS_CLIENT_ID": "claude-code-mac"})
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertEqual((client.rows, receipts[-1]["principal_fp"], receipts[-1]["outcome"]), ([], G.fingerprint("hermes-default"), "deny"))
        shared = DOC["clients"] + [{**hermes, "facts": {"os_user": "jimmarkunas"}}]                     # same OS user as the Mac client: neither can be told apart
        replies, receipts, client = call(tool_call(ok_item()), build(clients=shared))
        self.assertEqual((reason(replies), client.rows, receipts), ("identity_ambiguous", [], []))


class EveryHandlerIsGated(unittest.TestCase):
    """A contract test: a tool handler that skips authorization_gate fails here."""
    @staticmethod
    def calls_inside_gate(source, function, target, gate):
        """True when every call to `target` inside `function` is within a lambda that is an argument of a call to `gate`."""
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef,)) and n.name == function)
        parents = {child: node for node in ast.walk(fn) for child in ast.iter_child_nodes(node)}
        name = lambda c: c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
        hits = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and name(n) == target]
        def gated(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, ast.Call) and name(node) == gate:
                    return True
            return False
        return bool(hits) and all(gated(h) for h in hits)

    def test_the_stdio_handler_delivers_only_inside_the_gate(self):
        source = (ROOT / "lifeos/inbox/send_to_jim.py").read_text()
        self.assertTrue(self.calls_inside_gate(source, "governed", "deliver", "call"))
        self.assertIn("governed(", ast.get_source_segment(source, next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == "serve")))

    def test_the_buzz_handler_runs_a_tool_only_inside_the_gate(self):
        source = (ROOT / "lifeos/buzz/mcp.py").read_text()
        tree = ast.parse(source)
        handler = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "handler")
        rpcs = [n for n in ast.walk(handler) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "rpc"]
        self.assertEqual(len(rpcs), 2)                                                                  # one inside governed(...), one for the methods that are not tool calls
        parents = {child: node for node in ast.walk(handler) for child in ast.iter_child_nodes(node)}
        inside = [r for r in rpcs if any(isinstance(p, ast.Call) and getattr(p.func, "id", "") == "governed" for p in self._up(r, parents))]
        self.assertEqual(len(inside), 1)
        gov = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "governed")
        self.assertTrue(any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "call" for n in ast.walk(gov)))

    @staticmethod
    def _up(node, parents):
        while node in parents:
            node = parents[node]
            yield node


class SeededGrants(unittest.TestCase):
    def test_every_seed_is_complete_expiring_named_and_in_the_catalog(self):
        access = M.load()
        self.assertTrue(access.grants)
        rows = {(c["principal"], c["tool"], c["action"]) for c in CATALOG["capabilities"]}
        for g in access.grants:
            self.assertTrue(g.expires.tzinfo and g.expires > datetime(2026, 11, 22, tzinfo=timezone.utc) and (g.expires.year == 2027 or (g.tool.startswith("memory_") and g.expires.date().isoformat() == "2026-12-05")), g.id)   # LIFE-868: the synthetic memory grants end Dec 5 2026 (Jim, comment 13743), well before Feb 2027
            self.assertFalse(g.revoked, g.id)
            self.assertRegex(g.credential_ref, r"^[a-z][a-z0-9-]+$")
            self.assertEqual(len(g.actions), 1, g.id)
            self.assertTrue(all((g.principal, g.tool, a) in rows for a in g.actions), g.id)
        self.assertEqual(sorted({g.tool for g in access.grants}), ["agent_lookup", "buzz_capabilities", "buzz_publish", "buzz_read", "drive_read", "drive_search", "gmail_read", "gmail_search", "memory_correct", "memory_recall", "memory_revoke", "memory_store", "send_to_jim", "source_read"])
        writes = [g for g in access.grants if "write" in g.actions]                                                # Jim, Nov 22 2026 (option A): the only writes are today's Buzz publishing for the two chat clients, in the two communities each can write to
        self.assertEqual({(g.principal, g.tool, g.purpose, g.credential_ref) for g in writes},
                         {(p, "buzz_publish", "buzz-publish", "buzz-scoped-member-key") for p in ("claude-chat", "claude-chat-bytalos", "chatgpt-chat", "chatgpt-chat-bytalos", "muse-chat")}
                         | {(p, op, "synthetic-memory-test", "mcp-credential-" + p) for p in ("openclaw", "hermes-librarian") for op in ("memory_store", "memory_correct", "memory_revoke")}   # LIFE-868 (Jim, comment 13743): synthetic memory writes
                         | {(p, "send_to_jim", "agent-inbox", "notion-readonly-token") for p in ("mac-claude-code", "oracle-claude-code", "openclaw", "hermes-librarian", "claude-chat", "chatgpt-chat", "honcho-alerts", "muse-chat")})   # LIFE-876 (Jim, comments 13748 and 13823): the Agent Inbox, Personal only
        self.assertEqual({g.tenant for g in writes if g.tool == "send_to_jim"}, {"personal"})
        self.assertEqual(len(writes), 19)
        self.assertEqual(sorted(c["principal"] for c in CATALOG["capabilities"] if c["tool"] == "send_to_jim"), sorted(["mac-claude-code", "oracle-claude-code", "openclaw", "hermes-librarian", "claude-chat", "chatgpt-chat", "honcho-alerts", "muse-chat"]))   # a catalog row for exactly the six granted clients, none for the stdio Mac row or a Bytalos principal

    def test_the_shipped_seed_lets_no_one_call_send_to_jim(self):
        """LIFE-865: no client has the send_to_jim MCP server configured, so the Mac client resolves but is refused before any inbox row."""
        replies, receipts, client = call(tool_call(ok_item()), M.load())
        self.assertEqual((reason(replies), client.rows, receipts[-1]["reason"]), ("denied_catalog_unknown_capability", [], "catalog_unknown_capability"))

    def test_muse_holds_exactly_the_four_personal_grants_jim_approved_and_nothing_on_gmail_drive_memory_or_bytalos(self):
        """LIFE-923 (Jim, M3, comment 13891): Buzz read and publish in life-os plus send_to_jim, Personal only, expiring Feb 22 2027."""
        access = M.load()
        mine = [g for g in access.grants if g.principal == "muse-chat"]
        self.assertEqual(sorted(g.tool for g in mine), ["buzz_capabilities", "buzz_publish", "buzz_read", "send_to_jim"])
        self.assertEqual({(g.tenant, g.expires.date().isoformat(), g.revoked) for g in mine}, {("personal", "2027-02-22", False)})
        self.assertEqual([c["facts"] for c in json.loads(M.FILE.read_text())["clients"] if c["id"] == "muse-chat"], [{"oauth_client": "muse"}])   # identified by the verified OAuth client label only
        self.assertEqual(M.identify(access, "http", {"oauth_client": "muse"})["id"], "muse-chat")
        with self.assertRaises(M.AccessError):                                                                    # an unknown client is refused
            M.identify(access, "http", {"oauth_client": "muse2"})

    def test_only_the_catalog_rows_a_live_call_exercised_are_working_and_every_other_seeded_row_stays_synthetic(self):
        """LIFE-865 live proof (Nov 22 2026, through the claude.ai Buzz connector against release 52343c06da4e): Claude's capabilities and Personal read, and its Bytalos read, were called live. Nothing else was: not ChatGPT, not a publish, not send_to_jim."""
        new = [c for c in CATALOG["capabilities"] if c["as_of"] == "2026-11-22"]
        self.assertTrue(new)
        live = {(c["principal"], c["tool"]) for c in new if c["status"] == "working"}
        self.assertEqual(live, {("claude-chat", "buzz_capabilities"), ("claude-chat", "buzz_read"), ("claude-chat-bytalos", "buzz_read")})
        self.assertEqual({c["status"] for c in new if (c["principal"], c["tool"]) not in live}, {"synthetic"})
        self.assertEqual({c["status"] for c in new if c["tool"] in ("buzz_publish", "send_to_jim")} - {"synthetic"}, set())       # no write is live-proven
        out = G.Outcome("allow", "ok", True)
        self.assertFalse(out.live_proven)                                                                                         # the gate's own flag stays False: the catalog row status is the live-proof ledger


class ProposedPublishGrants(unittest.TestCase):
    """The four Buzz publish grants of docs/platform/mcp-grants-proposed.md (Jim's decision, option A, Nov 22 2026 6:14 PM CT) are active, valid, and exactly as wide as the read configuration: two clients by two tenants of buzz_publish, nothing else."""
    DOC = (ROOT / "docs/platform/mcp-grants-proposed.md").read_text()
    BLOCKS = re.findall(r"```json\n(.*?)\n```", DOC, re.S)

    def test_they_are_active_and_nothing_wider_than_two_clients_by_two_tenants_of_buzz_publish(self):
        grants, rows = json.loads(self.BLOCKS[0]), json.loads(self.BLOCKS[1])
        self.assertEqual(len(grants), 4)
        active = {g["id"] for g in DOC["grants"]}
        self.assertEqual(active & {g["id"] for g in grants}, {g["id"] for g in grants})
        self.assertEqual([c for c in CATALOG["capabilities"] if c["tool"] == "buzz_publish" and c["principal"] != "muse-chat"], rows)                    # Muse's own publish grant (LIFE-923, Jim's M3) is pinned by its own test
        self.assertEqual([g for g in DOC["grants"] if g["tool"] == "buzz_publish" and g["principal"] != "muse-chat"], grants)                                   # exactly these, no other publish grant
        self.assertEqual({(g["principal"], g["tenant"], g["tool"], tuple(g["actions"]), g["purpose"], g["expires"]) for g in grants},
                         {(p, t, "buzz_publish", ("write",), "buzz-publish", "2027-02-22") for p, t in (("claude-chat", "personal"), ("claude-chat-bytalos", "bytalos"), ("chatgpt-chat", "personal"), ("chatgpt-chat-bytalos", "bytalos"))})
        self.assertEqual({r["status"] for r in rows}, {"synthetic"})
        self.assertEqual({r["tool"] for r in rows}, {"buzz_publish"})

    def test_activated_they_allow_exactly_those_four_and_nothing_else(self):
        access = build()
        for client, tenant in (("claude-chat", "personal"), ("claude-chat", "bytalos"), ("chatgpt-chat", "personal"), ("chatgpt-chat", "bytalos")):
            row = M.identify(access, "http", {"oauth_client": client.split("-")[0]})
            out = M.call(access, row, tenant, "buzz_publish", "write", "buzz", "buzz", lambda: {"ok": True}, [].append, NOW)
            self.assertEqual((out.decision, out.reason), ("allow", "ok"), (client, tenant))
        row = M.identify(access, "http", {"oauth_client": "claude"})
        self.assertEqual(M.call(access, row, "tgp", "buzz_publish", "write", "buzz", "buzz", lambda: {}, [].append, NOW).decision, "deny")      # no other tenant
        self.assertEqual(M.call(access, row, "personal", "buzz_publish", "send", "buzz", "buzz", lambda: {}, [].append, NOW).decision, "deny")   # no other action


if __name__ == "__main__":
    unittest.main()
