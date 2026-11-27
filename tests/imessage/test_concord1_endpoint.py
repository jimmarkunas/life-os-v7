"""(synthetic): the private endpoint's `concord1` request, the Concord helper behind it, and the reader on the runner
side.
"""

import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from lifeos.edge.concord import concord_helper as helper
from lifeos.imessage import alerts_endpoint as ep
from lifeos.inbox import concord_reader, sources
from tests.imessage.test_alerts import OTHER, RUNNER, v2, whois

REPLY = b'{"at":1795600000,"code":"","messages":2,"ok":true}\n'


class Endpoint(unittest.TestCase):
    def call(self, payload, answer=REPLY, uid=0):
        out, seen = io.BytesIO(), []

        def concord(frame):
            seen.append(frame)
            if isinstance(answer, Exception):
                raise answer
            return answer

        code = ep.main(io.BytesIO(payload), out, whois, "unused", "unused", uid=lambda: uid, concord=concord)
        return code, out.getvalue(), seen

    def test_a_tagged_runner_gets_the_helpers_one_line_and_only_the_fixed_line_is_forwarded(self):
        self.assertEqual(self.call(v2(RUNNER) + b"concord1\n"), (0, REPLY, [b"concord1\n"]))

    def test_refusals_never_reach_the_helper(self):
        for payload, uid in (
            (v2(OTHER) + b"concord1\n", 0),
            (b"concord1\n", 0),
            (v2(RUNNER) + b"concord1\n", 1001),
            (v2(RUNNER) + b"concord1 7\n", 0),
            (v2(RUNNER) + b"concord2\n", 0),
            (v2(RUNNER) + b"concord1x\n", 0),
        ):
            code, out, seen = self.call(payload, uid=uid)
            self.assertEqual((code, out, seen), (1, b"", []), payload[-14:])

    def test_a_helper_failure_answers_nothing(self):
        self.assertEqual(self.call(v2(RUNNER) + b"concord1\n", OSError("down"))[:2], (1, b""))

    def test_the_forwarder_round_trips_over_a_real_unix_socket_and_rejects_anything_that_is_not_one_json_line(self):
        path = os.path.join(tempfile.mkdtemp(), "c.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(2)

        def serve(reply):
            conn, _ = server.accept()
            while conn.recv(64):
                pass
            conn.sendall(reply)
            conn.close()

        for reply, ok in ((REPLY, True), (b"HTTP/1.1 200 OK\r\n\r\n", False), (b'{"ok":true}', False)):
            t = threading.Thread(target=serve, args=(reply,))
            t.start()
            if ok:
                self.assertEqual(ep.concord1(b"concord1\n", path, 5), reply)
            else:
                with self.assertRaises(ValueError):
                    ep.concord1(b"concord1\n", path, 5)
            t.join()
        server.close()


def cli(stdout="", code=0, error=None):
    def run(cmd, **kwargs):
        run.seen = (cmd, kwargs)
        if error:
            raise error
        return subprocess.CompletedProcess(cmd, code, stdout, "")

    return run


MESSAGE = {
    "messageId": "m-1",
    "senderAgentId": "agent:SECRET-SENDER",
    "recipientAgentId": "agent:b",
    "content": "PRIVATE MESSAGE TEXT /home/secret/path",
    "createdAt": "2026-11-25T10:00:00Z",
    "status": "delivered",
}
NOTICE = "Concord sends product and coordination telemetry (no code, paths, messages, or task content).\n"


class Helper(unittest.TestCase):
    def ask(self, run, raw=b"concord1\n"):
        return helper.answer(raw, run, lambda: 1795600000)

    def test_a_good_read_is_a_count_and_nothing_else_and_never_carries_message_content(self):
        out = io.BytesIO()
        helper.main(io.BytesIO(b"concord1\n"), out, cli(NOTICE + json.dumps([MESSAGE, MESSAGE])), lambda: 1795600000)
        self.assertEqual(json.loads(out.getvalue()), {"at": 1795600000, "code": "", "messages": 2, "ok": True})
        for secret in ("PRIVATE", "SECRET", "/home", "m-1", "agent"):
            self.assertNotIn(secret, out.getvalue().decode())
        self.assertTrue(out.getvalue().endswith(b"\n"))

    def test_the_cli_is_run_read_only_with_a_timeout_and_without_telemetry(self):
        run = cli("[]")
        self.ask(run)
        cmd, kwargs = run.seen
        self.assertEqual(cmd[1:], ["-C", helper.REPO, "messages", "--json"])
        self.assertEqual((kwargs["timeout"], kwargs["env"]["CONCORD_TELEMETRY_DISABLED"]), (helper.WAIT, "1"))

    def test_fixed_codes_for_every_failure_and_never_text(self):
        self.assertEqual(self.ask(cli(error=FileNotFoundError("/x/concord")))["code"], "CONCORD_CLI_MISSING")
        self.assertEqual(self.ask(cli(error=subprocess.TimeoutExpired("c", 25)))["code"], "CONCORD_TIMEOUT")
        self.assertEqual(self.ask(cli(error=PermissionError("nope")))["code"], "CONCORD_READ_FAILED")
        self.assertEqual(self.ask(cli("x", code=2))["code"], "CONCORD_READ_FAILED")
        for bad in ("no array here", "[not json", json.dumps({"a": 1}), json.dumps([{"messageId": 1}])):
            self.assertEqual(self.ask(cli(bad))["code"], "CONCORD_BAD_OUTPUT", bad)
        self.assertEqual(self.ask(cli("[]"), b"concord1 x\n")["code"], "CONCORD_BAD_FRAME")
        for reply in (self.ask(cli(error=FileNotFoundError("/secret/path"))), self.ask(cli("garbage SECRET"))):
            self.assertEqual(set(reply), {"ok", "code", "messages", "at"})
            self.assertNotIn("SECRET", json.dumps(reply))


class Reader(unittest.TestCase):
    def test_only_a_well_formed_reply_is_believed(self):
        self.assertEqual(concord_reader.health("h", lambda h, r: REPLY)["messages"], 2)
        for raw in (
            b"",
            b"{}",
            b'{"ok":true,"code":"X","messages":1,"at":1}',
            b'{"ok":false,"code":"","messages":0,"at":1}',
            b'{"ok":true,"code":"","messages":true,"at":1}',
            b'{"ok":false,"code":"CONCORD_ANYTHING","messages":0,"at":1}',
            b"not json",
        ):
            self.assertEqual(
                concord_reader.health("h", lambda h, r, raw=raw: raw),
                {"ok": False, "code": "CONCORD_UNREACHABLE", "messages": 0, "at": 0},
                raw,
            )
        self.assertEqual(
            concord_reader.health("h", lambda h, r: (_ for _ in ()).throw(OSError("down")))["code"],
            "CONCORD_UNREACHABLE",
        )

    def test_the_request_is_the_one_fixed_line(self):
        seen = []
        concord_reader.health("h", lambda h, r: seen.append(r) or REPLY)
        self.assertEqual(seen, [b"concord1\n"])

    def test_concord_is_the_only_source_allowed_to_read_live_and_every_other_default_reader_still_refuses(self):
        self.assertEqual(sources.LIVE_READ_SOURCES, frozenset({"concord"}))
        self.assertFalse(sources.LIVE_READS_ENABLED)
        for name in ("jira", "calendar", "todoist", "asana", "followup"):
            self.assertFalse(sources.live_allowed(name), name)
        root = Path(__file__).resolve().parents[2] / "lifeos" / "inbox"
        for path in sorted(root.glob("*.py")):
            if path.name in ("sources.py", "concord_reader.py"):
                continue
            self.assertNotIn(
                "LIVE_READ_SOURCES", path.read_text(), path.name
            )  # no other module widens or reads the allow-list
        self.assertIn("refuse_live", (root / "followup.py").read_text())
        with self.assertRaises(sources.InboxError) as caught:
            sources.refuse_live("cred:x")
        self.assertEqual(str(caught.exception), "live_reads_not_authorized")

    def test_the_reader_and_the_helper_have_no_write_path(self):
        root = Path(__file__).resolve().parents[2]
        for rel in ("lifeos/inbox/concord_reader.py", "lifeos/edge/concord/concord_helper.py"):
            text = (root / rel).read_text()
            for verb in ('"send"', '"reply"', '"resume"', '"prompt"', "messages send", "concord send"):
                self.assertNotIn(verb, text, rel)


if __name__ == "__main__":
    unittest.main()
