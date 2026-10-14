import hashlib
import ipaddress
import json
import os
import pathlib
import plistlib
import re
import shlex
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from lifeos.imessage import attention_mcp, openclaw_imessage as w

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAC = ROOT / "lifeos/imessage/mac"
RELAY_SH = (MAC / "relay.sh").read_text()
ORACLE_V4, ORACLE_V6, OTHER_V4 = "100.64.0.7", "fd7a:115c:a1e0::7", "100.64.0.8"     # invented tailnet addresses
SIG = b"\r\n\r\n\x00\r\nQUIT\n"
CC = shlex.split(os.environ.get("LIFEOS_TEST_CC", "")) or ([shutil.which("cc")] if shutil.which("cc") else [])


def heredoc(name):
    return re.search(r"<<'%s'[^\n]*\n(.*?)\n%s\n" % (name, name), RELAY_SH, re.S).group(1)


def proxy_v2(src, cmd=0x21, port=47321):
    if ":" in src:
        return SIG + bytes([cmd, 0x21]) + struct.pack("!H", 36) + ipaddress.IPv6Address(src).packed + ipaddress.IPv6Address("::1").packed + struct.pack("!HH", 50000, port)
    return SIG + bytes([cmd, 0x11]) + struct.pack("!H", 12) + ipaddress.IPv4Address(src).packed + ipaddress.IPv4Address("127.0.0.1").packed + struct.pack("!HH", 50000, port)


def ask(port, payload):
    data = b""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        try:
            s.sendall(payload)
            s.shutdown(socket.SHUT_WR)
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        except (BrokenPipeError, ConnectionResetError):
            pass
    return data


class Inetd:
    """launchd's inetdCompatibility, emulated: one process per accepted connection, the connection as its stdin/stdout."""

    def __init__(self, listener, argv, env=None):
        self.listener, self.argv, self.env = listener, argv, env
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            with conn:
                subprocess.run(self.argv, stdin=conn, stdout=conn, stderr=subprocess.DEVNULL, env=self.env, timeout=20)


class FakeServe:
    """Tailscale Serve's TCP forwarder with PROXY v2, emulated as observed live: prepend the (authenticated) source, copy both directions at once,
    and tear the whole connection down when the client half-closes (Serve does not keep a half-open connection)."""

    def __init__(self, target_port, src):
        self.target_port, self.src = target_port, src
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self.forward, args=(client,), daemon=True).start()

    def forward(self, client):
        try:
            up = socket.create_connection(("127.0.0.1", self.target_port), timeout=10)
            up.sendall(proxy_v2(self.src))
        except OSError:
            client.close()
            return

        def pump(src, dst):
            try:
                while True:
                    chunk = src.recv(4096)
                    if not chunk:
                        break
                    dst.sendall(chunk)
            except OSError:
                pass
            for s in (client, up):                                # either side ending ends both, like Serve
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        back = threading.Thread(target=pump, args=(up, client), daemon=True)
        back.start()
        pump(client, up)
        back.join(10)
        client.close()
        up.close()


@unittest.skipUnless(CC, "no C compiler (set LIFEOS_TEST_CC)")
class Relay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(os.path.join(cls.home, "Library/Messages"))
        db = os.path.join(cls.home, "Library/Messages/chat.db")                      # a synthetic Messages database the real helper tool reads
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, date INTEGER, is_from_me INTEGER, is_read INTEGER, item_type INTEGER)")
        con.execute("INSERT INTO message (guid, text, date, is_from_me, is_read, item_type) VALUES ('guid-SECRET', 'body SECRET', ?, 0, 0, 0)",
                    (attention_mcp._ticks(datetime.now(timezone.utc) - timedelta(hours=1)),))
        con.commit()
        con.close()
        cls.helper_sock = os.path.join(cls.tmp, "h.sock")
        cls.helper = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        cls.helper.bind(cls.helper_sock)
        cls.helper.listen(8)
        Inetd(cls.helper, [sys.executable, "-I", str(ROOT / "lifeos/imessage/attention_mcp.py")], env=dict(os.environ, HOME=cls.home))
        cls.relay = os.path.join(cls.tmp, "relay")
        subprocess.run(CC + ["-O2", "-Wall", "-Wextra", "-Werror", '-DALLOWED_V4="%s"' % ORACLE_V4, '-DALLOWED_V6="%s"' % ORACLE_V6,
                             '-DTEST_HELPER_SOCKET="%s"' % cls.helper_sock, "-o", cls.relay, str(MAC / "relay.c")], check=True, capture_output=True)
        cls.listener = socket.create_server(("127.0.0.1", 0))
        cls.port = cls.listener.getsockname()[1]
        Inetd(cls.listener, [cls.relay])

    @classmethod
    def tearDownClass(cls):
        cls.listener.close()
        cls.helper.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_authorized_oracle_gets_exactly_the_helper_reply(self):
        for src in (ORACLE_V4, ORACLE_V6):
            reply = json.loads(ask(self.port, proxy_v2(src) + b"attention\n"))
            state = reply["result"]["structuredContent"]
            self.assertEqual((state["attention"], state["health"], state["count"]), ("NEEDS_ATTENTION", "CURRENT", None))

    def test_the_installers_negative_checks_all_pass_against_the_real_relay(self):
        run = subprocess.run([sys.executable, "-I", "-", str(self.port), ORACLE_V4], input=heredoc("PROBE_PY"), capture_output=True, text=True, timeout=60)
        rows = [line.split("\t") for line in run.stdout.splitlines()]
        self.assertEqual(len(rows), 8)
        self.assertEqual({status for _, status in rows}, {"PASS"}, rows)

    def test_forged_identity_inside_the_request_is_refused(self):
        self.assertEqual(ask(self.port, proxy_v2(OTHER_V4) + proxy_v2(ORACLE_V4) + b"attention\n"), b"")
        self.assertEqual(ask(self.port, proxy_v2(ORACLE_V6)[:16] + b"\xff\xff" + b"attention\n"), b"")      # absurd length

    def test_full_chain_through_the_oracle_wrapper(self):
        serve = FakeServe(self.port, ORACLE_V4)
        out = w.attention(path=self.endpoint(serve.port))
        self.assertEqual(set(out), w.KEYS)
        self.assertEqual((out["attention"], out["health"], out["sync"], out["count"]), ("NEEDS_ATTENTION", "CURRENT", "UNVERIFIED", None))
        self.assertNotIn("SECRET", json.dumps(out))
        other = FakeServe(self.port, OTHER_V4)
        self.assertEqual(w.attention(path=self.endpoint(other.port))["health"], "DISCONNECTED")      # an unauthorized source gets nothing
        serve.listener.close()
        other.listener.close()

    def test_a_half_closing_client_loses_the_reply_through_serve(self):
        serve = FakeServe(self.port, ORACLE_V4)
        with socket.create_connection(("127.0.0.1", serve.port), timeout=10) as s:
            s.sendall(b"attention\n")
            s.shutdown(socket.SHUT_WR)                            # what the first wrapper did: Serve drops the connection before the relay answers
            self.assertEqual(s.recv(4096), b"")
        serve.listener.close()

    def test_forged_identity_through_serve_is_refused(self):
        serve = FakeServe(self.port, ORACLE_V4)                    # Serve's genuine header comes first; a forged one is just a wrong request
        for payload in (proxy_v2(ORACLE_V4) + b"attention\n", proxy_v2(OTHER_V4) + b"attention\n", b"send\n", b"attentionXXXXXX\n"):
            with socket.create_connection(("127.0.0.1", serve.port), timeout=10) as s:
                s.settimeout(15)
                s.sendall(payload)
                self.assertEqual(s.recv(4096), b"", payload)
        serve.listener.close()

    def endpoint(self, port):
        path = os.path.join(self.tmp, "endpoint-%d" % port)
        with open(path, "w") as f:
            f.write("127.0.0.1 %d\n" % port)
        return path


@unittest.skipUnless(CC, "no C compiler (set LIFEOS_TEST_CC)")
class RelayHelperFailures(unittest.TestCase):
    def relay_with(self, helper_reply):
        tmp = tempfile.mkdtemp()
        sock = os.path.join(tmp, "h.sock")
        if helper_reply is not None:
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(sock)
            server.listen(1)

            def answer():
                conn, _ = server.accept()
                with conn:
                    conn.recv(4096)
                    conn.sendall(helper_reply)
            threading.Thread(target=answer, daemon=True).start()
        relay = os.path.join(tmp, "relay")
        subprocess.run(CC + ['-DALLOWED_V4="%s"' % ORACLE_V4, '-DALLOWED_V6="%s"' % ORACLE_V6, '-DTEST_HELPER_SOCKET="%s"' % sock, "-o", relay, str(MAC / "relay.c")],
                       check=True, capture_output=True)
        run = subprocess.run([relay], input=proxy_v2(ORACLE_V4) + b"attention\n", capture_output=True, timeout=20)
        return run.returncode, run.stdout

    def test_oversized_multiline_partial_or_missing_helper_replies_forward_nothing(self):
        for reply in (b"x" * 3000 + b"\n", b"{}\n{}\n", b'{"partial": ', b"", None):
            code, out = self.relay_with(reply)
            self.assertEqual((code, out), (1, b""), reply)

    def test_a_good_single_line_is_forwarded_unchanged(self):
        self.assertEqual(self.relay_with(b'{"ok": 1}\n'), (0, b'{"ok": 1}\n'))


class RelayInstaller(unittest.TestCase):
    def test_pinned_hashes_match_the_reviewed_sources(self):
        for name, path in (("SHA_RELAY", MAC / "relay.c"), ("SHA_AGENT", MAC / "ai.lifeos.imessage-relay.plist")):
            self.assertEqual(re.search(r'^%s="([0-9a-f]{64})"' % name, RELAY_SH, re.M).group(1), hashlib.sha256(path.read_bytes()).hexdigest(), name)

    def test_agent_is_on_demand_with_one_loopback_tcp_listener(self):
        agent = plistlib.loads((MAC / "ai.lifeos.imessage-relay.plist").read_bytes())
        self.assertEqual(set(agent), {"Label", "Program", "ProgramArguments", "inetdCompatibility", "Sockets", "ProcessType"})
        self.assertEqual(agent["Sockets"], {"Listener": {"SockType": "stream", "SockFamily": "IPv4", "SockNodeName": "127.0.0.1", "SockServiceName": "47321"}})
        self.assertEqual(agent["inetdCompatibility"], {"Wait": False})
        self.assertIn('LOCAL_PORT=47321', RELAY_SH)

    def test_never_touches_the_helper_privacy_settings_funnel_or_other_serve_ports(self):
        code = "\n".join(line for line in RELAY_SH.splitlines() if not line.lstrip().startswith("#"))
        for banned in (r"codesign (-f|--force|--sign|-s)", r"\$HELPER\"? *$", r"install[^\n]*\$HELPER", r"rm[^\n]*\$HELPER", r"\btccutil\b", r"\"\$TS\" funnel", r"--funnel",
                       r"serve reset", r"serve[^\n]*clear", r"\bssh\b", r"system\.run", r"openclaw"):
            self.assertIsNone(re.search(banned, code, re.M | re.I), banned)
        self.assertIn('"$TS" serve --bg --tcp "$TAILNET_PORT" --proxy-protocol 2 "tcp://127.0.0.1:$LOCAL_PORT"', code)
        self.assertIn('"$TS" serve --tcp "$TAILNET_PORT" off', code)
        self.assertIn("gate 15", code)

    def test_serve_state_classification(self):
        script = heredoc("SERVE_PY")

        def state(cfg):
            run = subprocess.run([sys.executable, "-I", "-", "47331", "47321", cfg], input=script, capture_output=True, text=True, timeout=10)
            return run.stdout.strip() if run.returncode == 0 else "unknown"
        ours = {"TCP": {"47331": {"TCPForward": "127.0.0.1:47321", "ProxyProtocol": 2}}}
        self.assertEqual(state("{}"), "free")
        self.assertEqual(state(""), "free")
        self.assertEqual(state(json.dumps(ours)), "ours")
        self.assertEqual(state(json.dumps({"TCP": {"443": {"HTTPS": True}}})), "free")                     # other ports are left alone
        self.assertEqual(state(json.dumps(dict(ours, AllowFunnel={"mac.example.ts.net:47331": True}))), "other")
        self.assertEqual(state(json.dumps({"TCP": {"47331": {"TCPForward": "127.0.0.1:47321"}}})), "other")   # no PROXY header
        self.assertEqual(state(json.dumps({"TCP": {"47331": {"TCPForward": "0.0.0.0:22", "ProxyProtocol": 2}}})), "other")
        self.assertEqual(state("not json"), "unknown")

    def test_listener_check_accepts_only_loopback(self):
        awk = re.search(r"/usr/bin/awk -v p=\"\.\$LOCAL_PORT\" '([^']*)'", RELAY_SH).group(1)

        def check(rows):
            head = "Active Internet connections (including servers)\nProto Recv-Q Send-Q  Local Address          Foreign Address        (state)\n"
            return subprocess.run(["awk", "-v", "p=.47321", awk], input=head + "\n".join(rows) + "\n", capture_output=True, text=True, timeout=10).returncode
        self.assertEqual(check(["tcp4       0      0  127.0.0.1.47321        *.*                    LISTEN"]), 0)
        self.assertEqual(check(["tcp46      0      0  *.47321                *.*                    LISTEN"]), 1)
        self.assertEqual(check(["tcp4       0      0  127.0.0.1.47321        *.*                    LISTEN", "tcp4  0 0  100.64.0.9.47321  *.*  LISTEN"]), 1)
        self.assertEqual(check(["tcp4       0      0  127.0.0.1.8080         *.*                    LISTEN"]), 1)        # nothing listening is a failure


NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)


def reply(**over):
    state = {"channel": "imessage", "attention": "NEEDS_ATTENTION", "count": None, "count_reliable": False, "health": "CURRENT", "sync": "UNVERIFIED",
             "observed_at": "2026-10-14T14:59:50Z", "source": "Apple Messages / Mac Mini"}
    state.update(over)
    body = {"content": [{"type": "text", "text": json.dumps(state)}], "structuredContent": state, "isError": state["health"] != "CURRENT"}
    return (json.dumps({"jsonrpc": "2.0", "id": 1, "result": body}) + "\n").encode()


class Wrapper(unittest.TestCase):
    def check(self, data):
        out = w.validate(data, NOW)
        self.assertEqual(set(out), w.KEYS)
        self.assertIsNone(out["count"])
        self.assertIs(out["count_reliable"], False)
        self.assertEqual(out["sync"], "UNVERIFIED")
        return out["attention"], out["health"]

    def test_a_valid_reply_passes_through_exactly(self):
        self.assertEqual(self.check(reply()), ("NEEDS_ATTENTION", "CURRENT"))
        self.assertEqual(self.check(reply(attention="UNKNOWN")), ("UNKNOWN", "CURRENT"))
        self.assertEqual(w.validate(reply(), NOW)["observed_at"], "2026-10-14T14:59:50Z")
        self.assertEqual(self.check(reply(attention="UNKNOWN", health="DISCONNECTED")), ("UNKNOWN", "DISCONNECTED"))

    def test_off_contract_replies_are_degraded(self):
        for bad in (reply(count=0), reply(count=3), reply(count_reliable=True), reply(attention="CLEAR"), reply(sync="CURRENT"), reply(health="STALE"),
                    reply(source="elsewhere"), reply(attention="NEEDS_ATTENTION", health="DISCONNECTED"), reply(observed_at="2026-10-14 14:59:50"),
                    reply().replace(b'"source"', b'"text": "body", "source"'), b"not json\n", b'{"jsonrpc": "2.0"}\n', reply() + reply(), reply()[:-1],
                    b" " * 3000 + b"\n"):
            self.assertEqual(self.check(bad), ("UNKNOWN", "DEGRADED"), bad[:80])

    def test_content_text_must_match_the_structured_state(self):
        good = json.loads(reply())
        good["result"]["content"][0]["text"] = json.dumps(dict(good["result"]["structuredContent"], extra="x"))
        self.assertEqual(self.check((json.dumps(good) + "\n").encode()), ("UNKNOWN", "DEGRADED"))

    def test_stale_or_future_observations_are_refused(self):
        self.assertEqual(self.check(reply(observed_at="2026-10-14T14:50:00Z")), ("UNKNOWN", "STALE"))
        self.assertEqual(self.check(reply(observed_at="2026-10-14T15:05:00Z")), ("UNKNOWN", "STALE"))

    def test_no_answer_or_no_endpoint_is_disconnected(self):
        self.assertEqual(self.check(b""), ("UNKNOWN", "DISCONNECTED"))

        def offline(host, port):
            raise socket.timeout()
        endpoint = os.path.join(tempfile.mkdtemp(), "endpoint")
        with open(endpoint, "w") as f:
            f.write("mac.example 47331\n")
        self.assertEqual(w.attention(NOW, endpoint, offline)["health"], "DISCONNECTED")
        self.assertEqual(w.attention(NOW, "/nonexistent/endpoint")["health"], "DISCONNECTED")
        with open(endpoint, "w") as f:
            f.write("mac.example 99999\n")
        self.assertEqual(w.attention(NOW, endpoint, offline)["health"], "DISCONNECTED")

    def test_only_the_single_attention_argument_runs(self):
        script = str(ROOT / "lifeos/imessage/openclaw_imessage.py")
        for args in ([], ["send"], ["attention", "--path", "/etc"], ["attention", "x"]):
            run = subprocess.run([sys.executable, "-I", script] + args, capture_output=True, text=True, timeout=10)
            self.assertEqual((run.returncode, run.stdout, run.stderr), (2, "usage: openclaw-imessage attention\n", ""), args)


if __name__ == "__main__":
    unittest.main()
