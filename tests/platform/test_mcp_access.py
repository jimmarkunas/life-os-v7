"""client identity is derived from trusted facts only, every refusal reason reaches a real handler, two clients on one
host cannot impersonate one.
"""

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
    """send_to_jim derives From from the authenticated client row, so a valid item names that client (the Mac client
    here) or omits From.
    """
    return inbox_item(**{"from": "claude-code-mac", **over})


def call(lines, access, facts=MAC, client=None, environ=None, now=NOW):
    out, receipts, client = io.StringIO(), [], client or FakeClient()
    with mock.patch("sys.stdout", out):
        send_to_jim.serve(
            lines, environ=environ, client=client, access=access, facts=facts, audit=receipts.append, now=now
        )
    return [json.loads(x) for x in out.getvalue().splitlines()], receipts, client


def tool_call(arguments, name="send_to_jim"):
    return [
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        )
    ]


def reason(replies):
    return json.loads(replies[0]["result"]["content"][0]["text"]).get("reason")


class Identity(unittest.TestCase):
    ACCESS = build()

    def test_a_trusted_fact_names_one_client_and_nothing_else_does(self):
        self.assertEqual(M.identify(self.ACCESS, "http", {"oauth_client": "claude"})["id"], "claude-chat")
        self.assertEqual(M.identify(self.ACCESS, "http", {"oauth_client": "chatgpt"})["id"], "chatgpt-chat")
        self.assertEqual(M.identify(self.ACCESS, "stdio", MAC)["id"], "claude-code-mac")
        for transport, facts in (
            ("http", {"oauth_client": "other"}),
            ("http", {}),
            ("stdio", {"os_user": "someone"}),
            ("stdio", {"oauth_client": "claude"}),
            ("http", {"os_user": "jimmarkunas"}),
        ):
            with self.assertRaises(M.AccessError) as caught:
                M.identify(self.ACCESS, transport, facts)
            self.assertEqual(str(caught.exception), "identity_unknown")

    def test_two_clients_that_share_every_fact_are_ambiguous_and_refused(self):
        clients = [
            {"id": "oracle-claude-code", "transport": "stdio", "facts": {"os_user": "ubuntu"}, "policy_key": "a" * 64},
            {"id": "hermes-default", "transport": "stdio", "facts": {"os_user": "ubuntu"}, "policy_key": "b" * 64},
        ]
        access = build(clients=DOC["clients"] + clients)
        with self.assertRaises(M.AccessError) as caught:
            M.identify(access, "stdio", {"os_user": "ubuntu"})
        self.assertEqual(str(caught.exception), "identity_ambiguous")

    def test_the_principal_belongs_to_one_tenant_and_an_unknown_tenant_is_refused(self):
        client = M.identify(self.ACCESS, "http", {"oauth_client": "claude"})
        self.assertEqual(
            (M.principal_id(client, "personal"), M.principal_id(client, "bytalos")),
            ("claude-chat", "claude-chat-bytalos"),
        )
        with self.assertRaises(M.AccessError):
            M.principal_id(client, "elsewhere")

    def test_an_unreadable_or_secret_bearing_file_is_never_a_partial_answer(self):
        for text in (
            "not json",
            "{}",
            json.dumps({**DOC, "version": 2}),
            json.dumps({**DOC, "clients": [{"id": "x", "transport": "stdio", "facts": {}, "policy_key": "c" * 64}]}),
            json.dumps({**DOC, "grants": [{**DOC["grants"][0], "credential_ref": "token=" + "A" * 24}]}),
        ):
            with self.assertRaises(M.AccessError) as caught:
                M.load(text, json.dumps(CATALOG))
            self.assertEqual(str(caught.exception), "governance_unavailable")
        with self.assertRaises(M.AccessError):
            M.load(json.dumps(DOC), "not json")


class SendToJim(unittest.TestCase):
    """The stdio server, through its real serve loop: the client is the OS user, never an argument, the sender field or
    the environment.
    """

    def test_a_granted_call_runs_and_the_receipt_names_tenant_and_purpose(self):
        replies, receipts, client = call(tool_call(ok_item()), build())
        self.assertFalse(replies[0]["result"]["isError"])
        self.assertEqual(len(client.rows), 1)
        allow = receipts[0]
        self.assertEqual(
            (
                allow["outcome"],
                allow["tenant"],
                allow["tool"],
                allow["purpose"],
                allow["action"],
                allow["principal_fp"],
            ),
            ("allow", "personal", "send_to_jim", "agent-inbox", "write", G.fingerprint("claude-code-mac")),
        )

    def test_every_refusal_reason_stops_before_the_inbox_row(self):
        def edit(**patch):
            return build(grants=lambda seeded: [{**g, **patch} if g["tool"] == "send_to_jim" else g for g in seeded])

        cases = (
            ("grant_revoked", edit(revoked=True), ok_item()),
            ("grant_expired", edit(expires="2026-11-01"), ok_item()),
            ("purpose_mismatch", edit(purpose="other"), ok_item()),
            ("no_grant", build(grants=lambda seeded: [g for g in seeded if g["tool"] != "send_to_jim"]), ok_item()),
            ("catalog_unknown_principal", build(), ok_item(workspace="Bytalos")),
            ("unknown_tenant", build(), ok_item(workspace="Elsewhere")),
        )
        for why, access, item in cases:
            replies, receipts, client = call(tool_call(item), access)
            self.assertTrue(replies[0]["result"]["isError"], why)
            self.assertEqual(reason(replies), why if why == "unknown_tenant" else "denied_" + why)
            self.assertEqual(client.rows, [], why)
            if why != "unknown_tenant":
                self.assertEqual((receipts[-1]["outcome"], receipts[-1]["reason"]), ("deny", why))

    def test_a_grant_expiry_may_carry_a_time_and_is_refused_the_second_it_passes(self):
        from datetime import timedelta

        def run(delta):
            access = build(
                grants=lambda seeded: [
                    {**g, "expires": (NOW + delta).astimezone(timezone.utc).isoformat()}
                    if g["tool"] == "send_to_jim"
                    else g
                    for g in seeded
                ]
            )
            replies, receipts, client = call(tool_call(ok_item()), access)
            return replies, receipts, client

        replies, receipts, client = run(timedelta(minutes=2))
        self.assertFalse(replies[0]["result"].get("isError"), replies)
        self.assertEqual(len(client.rows), 1)
        replies, receipts, client = run(timedelta(seconds=-1))
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertEqual(
            (reason(replies), client.rows, receipts[-1]["reason"]), ("denied_grant_expired", [], "grant_expired")
        )

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
        other = [
            {"id": "other-agent", "transport": "stdio", "facts": {"os_user": "someoneelse"}, "policy_key": "d" * 64}
        ]
        access = build(clients=DOC["clients"] + other)
        item = ok_item(**{"from": "other-agent"})
        replies, receipts, client = call(
            tool_call(item), access, environ={"LIFEOS_CLIENT_ID": "other-agent", "LIFEOS_PRINCIPAL": "other-agent"}
        )
        self.assertEqual(
            (reason(replies), client.rows, receipts),
            (
                "from_spoofed",
                [],
                [
                    {
                        "tool": "send_to_jim",
                        "principal_fp": G.fingerprint("claude-code-mac"),
                        "outcome": "deny",
                        "reason": "from_spoofed",
                    }
                ],
            ),
        )  # the refusal is audited under the real client
        replies, receipts, client = call(
            tool_call(ok_item()), access, environ={"LIFEOS_CLIENT_ID": "other-agent", "LIFEOS_PRINCIPAL": "other-agent"}
        )
        self.assertEqual(
            receipts[0]["principal_fp"], G.fingerprint("claude-code-mac")
        )  # the environment names another client
        replies, receipts, client = call(
            tool_call(ok_item(**{"from": "other-agent"})),
            access,
            facts={"os_user": "someoneelse"},
            environ={"LIFEOS_CLIENT_ID": "claude-code-mac"},
        )
        self.assertEqual((reason(replies), client.rows), ("denied_catalog_unknown_principal", []))

    def test_two_clients_on_one_host_cannot_impersonate_one_another(self):
        """Each client has its own OS user"""
        hermes = {"id": "hermes-default", "transport": "stdio", "facts": {"os_user": "hermes"}, "policy_key": "e" * 64}
        access = build(clients=DOC["clients"] + [hermes])
        replies, receipts, client = call(
            tool_call(ok_item()), access, facts={"os_user": "hermes"}, environ={"LIFEOS_CLIENT_ID": "claude-code-mac"}
        )
        self.assertEqual(
            (reason(replies), client.rows, receipts[-1]["reason"], receipts[-1]["principal_fp"]),
            ("from_spoofed", [], "from_spoofed", G.fingerprint("hermes-default")),
        )
        replies, receipts, client = call(
            tool_call(ok_item(**{"from": "hermes-default"})),
            access,
            facts={"os_user": "hermes"},
            environ={"LIFEOS_CLIENT_ID": "claude-code-mac"},
        )
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertEqual(
            (client.rows, receipts[-1]["principal_fp"], receipts[-1]["outcome"]),
            ([], G.fingerprint("hermes-default"), "deny"),
        )
        shared = DOC["clients"] + [{**hermes, "facts": {"os_user": "jimmarkunas"}}]
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
        self.assertIn(
            "governed(",
            ast.get_source_segment(
                source,
                next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == "serve"),
            ),
        )

    def test_the_buzz_handler_runs_a_tool_only_inside_the_gate(self):
        source = (ROOT / "lifeos/buzz/mcp.py").read_text()
        tree = ast.parse(source)
        handler = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "handler")
        rpcs = [n for n in ast.walk(handler) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "rpc"]
        self.assertEqual(len(rpcs), 2)
        parents = {child: node for node in ast.walk(handler) for child in ast.iter_child_nodes(node)}
        inside = [
            r
            for r in rpcs
            if any(isinstance(p, ast.Call) and getattr(p.func, "id", "") == "governed" for p in self._up(r, parents))
        ]
        self.assertEqual(len(inside), 1)
        gov = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "governed")
        self.assertTrue(any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "call" for n in ast.walk(gov)))

    @staticmethod
    def _up(node, parents):
        while node in parents:
            node = parents[node]
            yield node


MEMORY_TOOLS = ("memory_ask", "memory_correct", "memory_recall", "memory_revoke", "memory_store")
MEMORY_CLIENTS = (
    "claude-chat",
    "chatgpt-chat",
    "muse-chat",
    "mac-claude-code",
    "oracle-claude-code",
    "hermes-librarian",
    "openclaw",
    "duet-chat",
)
MEMORY_CREDENTIALS = ("openclaw", "hermes-librarian", "mac-claude-code", "oracle-claude-code")


ORCHESTRATOR_TOOLS = ("agent_status", "agent_dispatch", "agent_messages")
TODOIST_TOOLS = (
    "todoist_comment_add",
    "todoist_comments",
    "todoist_projects",
    "todoist_task_complete",
    "todoist_task_create",
    "todoist_task_move",
    "todoist_task_reopen",
    "todoist_task_update",
    "todoist_tasks",
)
TODOIST_READS = ("todoist_tasks", "todoist_projects", "todoist_comments")
TODOIST_CLIENTS = (  # eight named clients only (Codex added by Jim, Nov 28 2026): not Duet, Muse or the alert and capture clients
    "claude-chat",
    "chatgpt-chat",
    "claude-code-mac",
    "mac-claude-code",
    "oracle-claude-code",
    "openclaw",
    "hermes-librarian",
    "codex",
)


class SeededGrants(unittest.TestCase):
    def test_every_seed_is_complete_expiring_named_and_in_the_catalog(self):
        access = M.load()
        self.assertTrue(access.grants)
        rows = {(c["principal"], c["tool"], c["action"]) for c in CATALOG["capabilities"]}
        for g in access.grants:
            self.assertTrue(
                g.expires.tzinfo
                and g.expires > datetime(2026, 11, 22, tzinfo=timezone.utc)
                and (
                    g.expires.year == 2027
                    or (g.tool.startswith("memory_") and g.expires.date().isoformat() == "2099-12-31")
                ),
                g.id,
            )  # no expiry in practice (2099), still revocable
            self.assertFalse(g.revoked, g.id)
            self.assertRegex(g.credential_ref, r"^[a-z][a-z0-9-]+$")
            self.assertEqual(len(g.actions), 1, g.id)
            self.assertTrue(all((g.principal, g.tool, a) in rows for a in g.actions), g.id)
        self.assertEqual(
            sorted({g.tool for g in access.grants}),
            [
                "agent_dispatch",
                "agent_lookup",
                "agent_messages",
                "agent_status",
                "buzz_capabilities",
                "buzz_publish",
                "buzz_read",
                "drive_read",
                "drive_search",
                "gmail_read",
                "gmail_search",
                "memory_ask",
                "memory_correct",
                "memory_recall",
                "memory_revoke",
                "memory_store",
                "send_to_jim",
                "source_read",
                *TODOIST_TOOLS,
            ],
        )
        writes = [g for g in access.grants if "write" in g.actions]
        self.assertEqual(
            {(g.principal, g.tool, g.purpose, g.credential_ref) for g in writes},
            {
                (p, "buzz_publish", "buzz-publish", "buzz-scoped-member-key")
                for p in (
                    "claude-chat",
                    "claude-chat-bytalos",
                    "chatgpt-chat",
                    "chatgpt-chat-bytalos",
                    "muse-chat",
                    "duet-chat",
                    "duet-chat-bytalos",
                )
            }
            | {
                (p, op, "personal-memory", "mcp-credential-" + p if p in MEMORY_CREDENTIALS else "honcho-memory-token")
                for p in MEMORY_CLIENTS
                for op in ("memory_store", "memory_correct", "memory_revoke")
            }
            | {
                ("jira-capture", t, "personal-memory", "honcho-memory-token")
                for t in ("memory_store", "memory_correct", "memory_revoke")
            }
            | {
                ("openclaw", "buzz_publish", "buzz-publish", "mcp-credential-openclaw")
            }  # OpenClaw's own pilot-only relay identity
            | {
                (p, "send_to_jim", "agent-inbox", "notion-readonly-token")
                for p in (
                    "mac-claude-code",
                    "oracle-claude-code",
                    "openclaw",
                    "hermes-librarian",
                    "claude-chat",
                    "chatgpt-chat",
                    "honcho-alerts",
                    "muse-chat",
                    "duet-chat",
                )
            }  # the Agent Inbox, Personal only
            | {
                (p, "agent_dispatch", "orchestrator-dispatch", "concord-gateway-socket")
                for p in ("claude-chat", "chatgpt-chat")
            }
            | {
                (p, op, "todoist-write", "todoist-access-token")
                for p in TODOIST_CLIENTS
                for op in TODOIST_TOOLS
                if op not in ("todoist_tasks", "todoist_projects", "todoist_comments")
            },
        )
        self.assertEqual({g.tenant for g in writes if g.tool == "send_to_jim"}, {"personal"})
        self.assertEqual(
            len(writes), 22 + 8 * 3 + 8 * 6
        )  # three memory writes for each of eight clients; six Todoist writes for each of eight
        self.assertEqual(
            sorted(c["principal"] for c in CATALOG["capabilities"] if c["tool"] == "send_to_jim"),
            sorted(
                [
                    "mac-claude-code",
                    "oracle-claude-code",
                    "openclaw",
                    "hermes-librarian",
                    "claude-chat",
                    "chatgpt-chat",
                    "honcho-alerts",
                    "muse-chat",
                    "duet-chat",
                ]
            ),
        )

    def test_the_shipped_seed_lets_no_one_call_send_to_jim(self):
        """no client has the send_to_jim MCP server configured, so the Mac client resolves but is refused before any
        inbox row.
        """
        replies, receipts, client = call(tool_call(ok_item()), M.load())
        self.assertEqual(
            (reason(replies), client.rows, receipts[-1]["reason"]),
            ("denied_catalog_unknown_capability", [], "catalog_unknown_capability"),
        )

    def test_codex_holds_personal_recall_discovery_and_todoist_only_expiring_within_ninety_days(self):
        """Jim, Nov 27 2026: verified Codex, Personal recall only, up to 90 days; no store, ask, publish or Bytalos. Nov 28 2026: the nine Todoist operations added."""
        access = M.load()
        mine = [g for g in access.grants if g.principal.startswith("codex")]
        self.assertEqual(
            {(g.principal, g.tenant, g.tool, g.actions) for g in mine},
            {("codex", "personal", "buzz_capabilities", ("read",)), ("codex", "personal", "memory_recall", ("read",))}
            | {("codex", "personal", op, ("read",) if op in TODOIST_READS else ("write",)) for op in TODOIST_TOOLS},
        )
        self.assertEqual({g.expires.date().isoformat() for g in mine}, {"2027-02-25"})
        row = [c for c in access.credentials if c["id"] == "codex"][0]
        self.assertEqual((row["buzz_identity"], row.get("write", False), row["expires"]), (None, False, "2027-02-25"))

    def test_the_five_memory_tools_are_granted_to_eight_clients_in_personal_only_with_the_personal_memory_purpose(self):
        """all governed agents share real non-sensitive Personal memory"""
        access = M.load()
        mine = [
            g for g in access.grants if g.purpose == "personal-memory" and g.principal not in ("jira-capture", "codex")
        ]  # the capture client and Codex (recall only, 90 days) are pinned by their own tests
        self.assertEqual({(g.principal, g.tool) for g in mine}, {(p, t) for p in MEMORY_CLIENTS for t in MEMORY_TOOLS})
        self.assertEqual(len(mine), 40)
        self.assertEqual(
            {(g.tenant, g.account, g.expires.date().isoformat(), g.revoked) for g in mine},
            {("personal", "honcho", "2099-12-31", False)},
        )
        self.assertEqual(
            {(g.tool, g.actions) for g in mine},
            {
                ("memory_ask", ("read",)),
                ("memory_recall", ("read",)),
                ("memory_store", ("write",)),
                ("memory_correct", ("write",)),
                ("memory_revoke", ("write",)),
            },
        )
        self.assertEqual(
            {
                g.tool: g.purpose
                for g in access.grants
                if g.tool.startswith("memory_") and g.purpose == "personal-memory"
            },
            {t: "personal-memory" for t in MEMORY_TOOLS},
        )
        self.assertEqual(
            set(json.loads(M.FILE.read_text())["tool_purpose"][t] for t in MEMORY_TOOLS), {"personal-memory"}
        )
        self.assertEqual(
            [
                g
                for g in access.grants
                if g.tool.startswith("memory_") and (g.tenant != "personal" or g.principal.endswith("bytalos"))
            ],
            [],
        )  # no Bytalos or TGP memory grant of any kind
        rows = {
            (c["principal"], c["tool"])
            for c in CATALOG["capabilities"]
            if c["tool"].startswith("memory_") and c["principal"] not in ("jira-capture", "codex")
        }
        self.assertEqual(rows, {(p, t) for p in MEMORY_CLIENTS for t in MEMORY_TOOLS})
        synthetic = {
            (c["principal"], c["tool"])
            for c in CATALOG["capabilities"]
            if c["tool"].startswith("memory_")
            and c["status"] == "synthetic"
            and c["principal"] not in ("jira-capture", "codex")
        }
        self.assertEqual(
            synthetic,
            {(p, "memory_ask") for p in MEMORY_CLIENTS if p != "mac-claude-code"}
            | {(p, t) for p in ("duet-chat", "muse-chat") for t in MEMORY_TOOLS},
        )

    def test_jira_capture_holds_exactly_store_correct_and_revoke_in_personal_and_nothing_else(self):
        """no recall, no ask, no Gmail, Drive, Buzz, source lookup or inbox"""
        access = M.load()
        mine = [g for g in access.grants if g.principal == "jira-capture"]
        self.assertEqual(
            {
                (g.tool, g.actions, g.tenant, g.account, g.purpose, g.expires.date().isoformat(), g.revoked)
                for g in mine
            },
            {
                (t, ("write",), "personal", "honcho", "personal-memory", "2027-02-22", False)
                for t in ("memory_store", "memory_correct", "memory_revoke")
            },
        )
        self.assertEqual(
            sorted(c["tool"] for c in CATALOG["capabilities"] if c["principal"] == "jira-capture"),
            ["memory_correct", "memory_revoke", "memory_store"],
        )
        self.assertEqual(
            {c["status"] for c in CATALOG["capabilities"] if c["principal"] == "jira-capture"}, {"synthetic"}
        )  # until its first live call
        cred = next(c for c in access.credentials if c["id"] == "jira-capture")
        self.assertEqual((cred["buzz_identity"], cred.get("write", False), cred["revoked"]), (None, False, False))

    def test_muse_holds_the_four_personal_grants_jim_approved_plus_the_five_memory_tools_and_nothing_on_gmail_drive_or_bytalos(
        self,
    ):
        """Buzz read and publish in life-os plus send_to_jim, Personal only, expiring Feb 22 2027."""
        access = M.load()
        mine = [g for g in access.grants if g.principal == "muse-chat"]
        self.assertEqual(
            sorted(g.tool for g in mine),
            sorted(["buzz_capabilities", "buzz_publish", "buzz_read", "send_to_jim", *MEMORY_TOOLS]),
        )  # added the five memory tools for every client
        self.assertEqual(
            {(g.tenant, g.expires.date().isoformat(), g.revoked) for g in mine if not g.tool.startswith("memory_")},
            {("personal", "2027-02-22", False)},
        )
        self.assertEqual(
            [c["facts"] for c in json.loads(M.FILE.read_text())["clients"] if c["id"] == "muse-chat"],
            [{"credential": "muse"}],
        )  # identified by its own bearer credential row only
        self.assertEqual(M.identify(access, "http", {"credential": "muse"})["id"], "muse-chat")
        with self.assertRaises(M.AccessError):  # an unknown client is refused
            M.identify(access, "http", {"credential": "muse2"})

    def test_duet_holds_exactly_the_grants_claude_holds_under_its_own_client_and_nothing_wider(self):
        """the claude client's Buzz read and publish in Personal and Bytalos plus send_to_jim in Personal, expiring Feb
        22 2027
        """
        access = M.load()
        shape = lambda principal: sorted(
            (g.tenant, g.tool, g.purpose, tuple(g.actions), g.credential_ref, g.expires.date().isoformat(), g.revoked)
            for g in access.grants
            if g.principal.startswith(principal) and g.tool not in ORCHESTRATOR_TOOLS + TODOIST_TOOLS
        )
        self.assertEqual(shape("duet-chat"), shape("claude-chat"))  # the same rows, the same scope, no widening
        self.assertEqual(
            sorted({g.tool for g in access.grants if g.principal.startswith("duet-chat")}),
            ["buzz_capabilities", "buzz_publish", "buzz_read", *MEMORY_TOOLS, "send_to_jim"],
        )
        self.assertEqual(
            [c["facts"] for c in json.loads(M.FILE.read_text())["clients"] if c["id"] == "duet-chat"],
            [{"oauth_client": "duet"}],
        )
        self.assertEqual(M.identify(access, "http", {"oauth_client": "duet"})["id"], "duet-chat")
        self.assertEqual(
            M.identify(access, "http", {"oauth_client": "claude"})["id"], "claude-chat"
        )  # the existing clients are unchanged
        self.assertEqual(M.identify(access, "http", {"oauth_client": "chatgpt"})["id"], "chatgpt-chat")
        with self.assertRaises(M.AccessError):
            M.identify(access, "http", {"oauth_client": "duet2"})

    LIVE_877 = {
        **{
            p: {
                "buzz_capabilities",
                "buzz_read",
                "memory_store",
                "memory_recall",
                "memory_correct",
                "memory_revoke",
                "send_to_jim",
                "source_read",
            }
            for p in ("mac-claude-code", "oracle-claude-code", "openclaw", "claude-chat", "chatgpt-chat")
        },
        "hermes-librarian": {
            "buzz_capabilities",
            "buzz_read",
            "memory_store",
            "memory_recall",
            "memory_correct",
            "memory_revoke",
            "send_to_jim",
            "source_read",
            "gmail_search",
            "gmail_read",
            "drive_search",
            "drive_read",
        },
        "hermes-librarian-bytalos": {"gmail_search", "gmail_read", "drive_search", "drive_read"},
        "claude-chat-bytalos": {"buzz_read"},
    }
    LIVE_877["mac-claude-code"] |= {"memory_ask", "agent_lookup"}
    LIVE_877["oracle-claude-code"] |= {"agent_lookup"}
    LIVE_877["claude-chat"] |= {"buzz_publish"}
    LIVE_877["chatgpt-chat"] |= {"buzz_publish"}

    def test_only_the_catalog_rows_a_live_call_exercised_are_working_and_every_other_seeded_row_stays_synthetic(self):
        """every other governed row stays synthetic."""
        governed = [
            c
            for c in CATALOG["capabilities"]
            if c["as_of"] >= "2026-11-22" and c["provider"] in ("buzz", "google", "honcho", "notion")
        ]
        self.assertTrue(governed)
        live = {(c["principal"], c["tool"]) for c in governed if c["status"] == "working"}
        self.assertEqual(
            live,
            {
                (p, t)
                for p, tools in self.LIVE_877.items()
                for t in tools
                if any(c["principal"] == p and c["tool"] == t for c in governed)
            },
        )
        self.assertEqual({c["status"] for c in governed if (c["principal"], c["tool"]) not in live}, {"synthetic"})
        out = G.Outcome("allow", "ok", True)
        self.assertFalse(out.live_proven)


class ProposedPublishGrants(unittest.TestCase):
    """The four Buzz publish grants of docs/platform/mcp-grants-proposed.md are active, valid, and exactly as wide as the
    read configuration: two clients by.
    """

    DOC = (ROOT / "docs/platform/mcp-grants-proposed.md").read_text()
    BLOCKS = re.findall(r"```json\n(.*?)\n```", DOC, re.S)

    def test_they_are_active_and_nothing_wider_than_two_clients_by_two_tenants_of_buzz_publish(self):
        grants, rows = json.loads(self.BLOCKS[0]), json.loads(self.BLOCKS[1])
        self.assertEqual(len(grants), 4)
        active = {g["id"] for g in DOC["grants"]}
        self.assertEqual(active & {g["id"] for g in grants}, {g["id"] for g in grants})
        self.assertEqual(
            [
                c
                for c in CATALOG["capabilities"]
                if c["tool"] == "buzz_publish"
                and c["principal"] not in ("muse-chat", "duet-chat", "duet-chat-bytalos", "openclaw")
            ],
            rows,
        )  # Muse's own publish grant is pinned by its own test
        self.assertEqual(
            [
                g
                for g in DOC["grants"]
                if g["tool"] == "buzz_publish"
                and g["principal"] not in ("muse-chat", "duet-chat", "duet-chat-bytalos", "openclaw")
            ],
            grants,
        )  # exactly these, no other publish grant
        self.assertEqual(
            {(g["principal"], g["tenant"], g["tool"], tuple(g["actions"]), g["purpose"], g["expires"]) for g in grants},
            {
                (p, t, "buzz_publish", ("write",), "buzz-publish", "2027-02-22")
                for p, t in (
                    ("claude-chat", "personal"),
                    ("claude-chat-bytalos", "bytalos"),
                    ("chatgpt-chat", "personal"),
                    ("chatgpt-chat-bytalos", "bytalos"),
                )
            },
        )
        self.assertEqual(
            {(r["principal"], r["status"]) for r in rows},
            {
                ("claude-chat", "working"),
                ("claude-chat-bytalos", "synthetic"),
                ("chatgpt-chat", "working"),
                ("chatgpt-chat-bytalos", "synthetic"),
            },
        )  # the two Personal rows have live receipts
        self.assertEqual({r["tool"] for r in rows}, {"buzz_publish"})

    def test_activated_they_allow_exactly_those_four_and_nothing_else(self):
        access = build()
        for client, tenant in (
            ("claude-chat", "personal"),
            ("claude-chat", "bytalos"),
            ("chatgpt-chat", "personal"),
            ("chatgpt-chat", "bytalos"),
        ):
            row = M.identify(access, "http", {"oauth_client": client.split("-")[0]})
            out = M.call(
                access, row, tenant, "buzz_publish", "write", "buzz", "buzz", lambda: {"ok": True}, [].append, NOW
            )
            self.assertEqual((out.decision, out.reason), ("allow", "ok"), (client, tenant))
        row = M.identify(access, "http", {"oauth_client": "claude"})
        self.assertEqual(
            M.call(access, row, "tgp", "buzz_publish", "write", "buzz", "buzz", lambda: {}, [].append, NOW).decision,
            "deny",
        )  # no other tenant
        self.assertEqual(
            M.call(
                access, row, "personal", "buzz_publish", "send", "buzz", "buzz", lambda: {}, [].append, NOW
            ).decision,
            "deny",
        )  # no other action


if __name__ == "__main__":
    unittest.main()


class OutputTimeRecheck(unittest.TestCase):
    """A read runs the complete gate again just before output: a grant revoked while the source answered gives none."""

    def setUp(self):
        self.access = build()
        self.row = M.identify(self.access, "http", {"oauth_client": "claude"})

    def read(self, effect, receipts):
        return M.call(
            self.access, self.row, "personal", "memory_recall", "read", "honcho", "honcho", effect, receipts.append, NOW
        )

    def test_a_grant_still_live_at_output_returns_the_answer(self):
        receipts = []
        out = self.read(lambda: {"items": []}, receipts)
        self.assertEqual((out.decision, out.executed), ("allow", True))
        self.assertEqual([r["outcome"] for r in receipts], ["allow", "ok"])

    def test_a_grant_revoked_while_the_source_answered_gives_a_deny_and_a_deny_receipt(self):
        receipts = []

        def revoke_meanwhile():
            self.access.grants = tuple(g for g in self.access.grants if getattr(g, "tool", None) != "memory_recall")
            return {"items": ["would leak"]}

        out = self.read(revoke_meanwhile, receipts)
        self.assertEqual(out.decision, "deny")
        self.assertFalse(getattr(out, "executed", False))
        self.assertEqual([r["outcome"] for r in receipts], ["allow", "ok", "deny"])

    def test_a_write_is_not_checked_twice(self):
        calls = []
        out = M.call(
            self.access,
            self.row,
            "personal",
            "memory_store",
            "write",
            "honcho",
            "honcho",
            lambda: calls.append(1) or {"ok": True},
            [].append,
            NOW,
        )
        self.assertEqual((out.decision, calls), ("allow", [1]))
