"""LIFE-987 (synthetic): the Concord gateway program with a fake `concord-mcp` over stdio. No Concord, no Oracle, no network."""
import io
import json
import unittest

from lifeos.edge.concord import gateway as G


class Pipe:
    def __init__(self, owner):
        self.owner = owner
        self.closed = False

    def write(self, line):
        self.owner.receive(json.loads(line))

    def flush(self):
        pass

    def close(self):
        self.closed = True


class Out:
    def __init__(self, owner):
        self.owner = owner

    def readline(self):
        return self.owner.queue.pop(0) if self.owner.queue else ""


class FakeConcord:
    """A scripted concord-mcp: `tools` maps a tool name to a function(arguments) -> result dict (or raises)."""
    last = None
    tools = {}
    fail_start = False

    def __init__(self, argv, **kwargs):
        if FakeConcord.fail_start:
            raise OSError("no cli")
        FakeConcord.last = self
        self.argv, self.env, self.calls, self.queue = argv, kwargs.get("env"), [], []
        self.stdin, self.stdout, self.terminated = Pipe(self), Out(self), False

    def receive(self, message):
        if "id" not in message:
            return
        if message["method"] == "initialize":
            self.queue.append(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"serverInfo": {"name": "concord-mcp"}}}) + "\n")
        elif message["method"] == "tools/call":
            name, args = message["params"]["name"], message["params"]["arguments"]
            self.calls.append((name, args))
            result = FakeConcord.tools[name](args)
            if result == "ERROR":
                reply = {"jsonrpc": "2.0", "id": message["id"], "result": {"isError": True, "content": [{"type": "text", "text": "boom"}]}}
            else:
                reply = {"jsonrpc": "2.0", "id": message["id"], "result": {"content": [{"type": "text", "text": json.dumps(result)}]}}
            self.queue.append(json.dumps(reply) + "\n")

    def terminate(self):
        self.terminated = True


def request(op, client="claude", **args):
    return (json.dumps({"op": op, "client": client, "args": args}) + "\n").encode()


WORKSPACE = {"agents": [
    {"agentId": "oc-oracle", "status": "live/active", "task": "LIFE-900 build", "lastSeenAt": "2026-11-25T23:00:00Z"},
    {"agentId": "mc01", "status": "offline"},
    {"agentId": "mac-mc02", "status": "live/idle"},
    {"agentId": "claude-code:cfd3385c", "status": "live/active"},
    {"agentId": "orchestrator-claude", "status": "live/active"},
]}


def go(raw, **tools):
    FakeConcord.tools = {"inspect_work": lambda a: WORKSPACE, **tools}
    return G.answer(raw, popen=FakeConcord)


class Frames(unittest.TestCase):
    def test_every_malformed_request_is_refused_before_concord_starts(self):
        FakeConcord.last = None
        for raw in (b"", b"not json\n", b'{"op":"agent_status","client":"claude","args":{}}', b"[]\n", request("delete_everything"), request("agent_status", client="muse"),
                    request("agent_status", extra=1), request("agent_dispatch", agent="oc"), request("agent_dispatch", agent="nobody", ticket="LIFE-1", text="x", idempotency_key="k"),
                    request("agent_dispatch", agent="oc", ticket="LIFE-1", text="x", idempotency_key="k" * 129), request("agent_messages", limit=0), request("agent_messages", limit=51),
                    request("agent_messages", cursor="-1"), b"x" * 30000 + b"\n"):
            reply = G.answer(raw, popen=FakeConcord)
            self.assertEqual(reply["ok"], False, raw[:40])
            self.assertIn(reply["reason"], ("bad_frame", "bad_request"))
        self.assertIsNone(FakeConcord.last)

    def test_the_concord_process_gets_the_orchestrator_identity_and_a_clean_environment(self):
        go(request("agent_status", client="chatgpt"))
        env = FakeConcord.last.env
        self.assertEqual((env["CONCORD_AGENT_ID"], env["CONCORD_REPO_ROOT"], env["CONCORD_TELEMETRY_DISABLED"]), ("orchestrator-chatgpt", "/home/concord/life-os-v7", "1"))
        self.assertEqual(set(env), {"PATH", "HOME", "CONCORD_REPO_ROOT", "CONCORD_TELEMETRY_DISABLED", "CONCORD_AGENT_ID"})
        self.assertEqual(FakeConcord.last.argv, ["/home/concord/.npm-global/bin/concord-mcp"])
        self.assertTrue(FakeConcord.last.terminated)

    def test_a_missing_cli_or_a_tool_error_is_a_fixed_word(self):
        FakeConcord.fail_start = True
        try:
            self.assertEqual(G.answer(request("agent_status"), popen=FakeConcord), {"ok": False, "reason": "concord_missing"})
        finally:
            FakeConcord.fail_start = False
        self.assertEqual(go(request("agent_status"), inspect_work=lambda a: "ERROR"), {"ok": False, "reason": "concord_refused"})


class Status(unittest.TestCase):
    def test_all_four_agents_are_listed_with_presence_and_the_others_by_id_only(self):
        reply = go(request("agent_status"))
        rows = {r["agent"]: r for r in reply["data"]["agents"] if r["agent"]}
        self.assertEqual(list(rows), ["oc", "mc01", "mc02", "codex"])
        self.assertEqual((rows["oc"]["online"], rows["oc"]["id"], rows["oc"]["claim"]), (True, "oc-oracle", "LIFE-900 build"))
        self.assertEqual((rows["mc01"]["online"], rows["mc02"]["online"], rows["codex"]["online"], rows["codex"]["id"]), (False, True, False, ""))
        others = [r["id"] for r in reply["data"]["agents"] if not r["agent"]]
        self.assertEqual(others, ["claude-code:cfd3385c"])                                         # the orchestrator's own ids are not listed


class Dispatch(unittest.TestCase):
    ARGS = dict(agent="oc", ticket="LIFE-987", text="Please look at the gateway test.", idempotency_key="idem-1")

    def sent(self, state):
        sent = []

        def prompt(args):
            sent.append(args)
            return {"messageId": "m-1", "deliveryStatus": state}
        reply = go(request("agent_dispatch", **self.ARGS), update_work=prompt)
        return reply, sent

    def test_delivered_and_acknowledged_are_reported_as_concord_reports_them(self):
        for state in ("delivered", "acknowledged"):
            reply, sent = self.sent(state)
            self.assertEqual((reply["ok"], reply["data"]["delivered"], reply["data"]["state"], reply["data"]["message"]), (True, True, state, "m-1"))
            self.assertEqual(len(sent), 1)
        call = sent[0]
        self.assertEqual((call["operation"], call["to_agent_id"], call["idempotency_key"], call["delivery_mode"]), ("prompt", "oc-oracle", "idem-1", "steer"))
        self.assertIn("untrusted data", call["content"])
        self.assertIn("approves nothing new", call["content"])
        self.assertTrue(call["content"].startswith("[orchestrator-claude to oc] Dispatch for LIFE-987."))

    def test_queued_for_pull_is_not_delivered_and_an_unknown_state_is_never_called_delivered(self):
        reply, _ = self.sent("queued_pull")
        self.assertEqual(reply["data"], {"delivered": False, "reason": "agent_offline", "state": "not_delivered", "agent": "oc"})
        reply, _ = self.sent("something_new")
        self.assertEqual((reply["data"]["delivered"], reply["data"]["reason"], reply["data"]["state"]), (False, "delivery_unknown", "unknown"))

    def test_an_agent_that_is_not_online_gets_no_prompt_at_all(self):
        sent = []
        reply = go(request("agent_dispatch", **{**self.ARGS, "agent": "mc01"}), update_work=lambda a: sent.append(a) or {})
        self.assertEqual((reply["data"]["reason"], sent), ("agent_offline", []))
        reply = go(request("agent_dispatch", **{**self.ARGS, "agent": "codex"}), update_work=lambda a: sent.append(a) or {})
        self.assertEqual((reply["data"]["reason"], sent), ("agent_offline", []))

    def test_two_live_sessions_of_one_name_are_ambiguous_not_a_guess(self):
        both = {"agents": [{"agentId": "oc", "status": "live/active"}, {"agentId": "oc-oracle", "status": "live/idle"}]}
        FakeConcord.tools = {"inspect_work": lambda a: both, "update_work": lambda a: {}}
        self.assertEqual(G.answer(request("agent_dispatch", **self.ARGS), popen=FakeConcord), {"ok": False, "reason": "agent_ambiguous"})


class Messages(unittest.TestCase):
    def test_the_orchestrators_inbox_is_newest_first_clipped_and_paged(self):
        inbox = {"inbox": [{"messageId": f"m{i}", "senderAgentId": "oc-oracle", "createdAt": f"2026-11-25T10:0{i}:00Z", "status": "read", "content": "x" * 900} for i in range(5)]}
        calls = []
        first = go(request("agent_messages", limit=2), inspect_work=lambda a: calls.append(a) or inbox)
        self.assertEqual(calls[0], {"agent_id": "orchestrator-claude"})
        self.assertEqual([m["id"] for m in first["data"]["messages"]], ["m4", "m3"])
        self.assertEqual((len(first["data"]["messages"][0]["text"]), first["data"]["next_cursor"]), (G.CONTENT_MAX, "2"))
        last = go(request("agent_messages", limit=2, cursor="4"), inspect_work=lambda a: inbox)
        self.assertEqual(([m["id"] for m in last["data"]["messages"]], last["data"]["next_cursor"]), (["m0"], None))


class Main(unittest.TestCase):
    def test_one_request_line_in_one_reply_line_out(self):
        FakeConcord.tools = {"inspect_work": lambda a: WORKSPACE}
        out = io.BytesIO()
        self.assertEqual(G.main(io.BytesIO(request("agent_status")), out, popen=FakeConcord), 0)
        lines = out.getvalue().split(b"\n")
        self.assertEqual((len(lines), json.loads(lines[0])["ok"], lines[1]), (2, True, b""))


if __name__ == "__main__":
    unittest.main()
