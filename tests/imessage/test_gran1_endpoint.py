import io
import pathlib
import json
import os
import socket
import tempfile
import threading
import unittest

from lifeos.imessage import alerts_endpoint as ep
from tests.imessage.test_alerts import OTHER, RUNNER, v2, whois

BODY = json.dumps({"op": "list", "nonce": "n1"}).encode()
GOOD = b"gran1 %d\n%s" % (len(BODY), BODY)


class Gran1(unittest.TestCase):
    def call(self, payload, answer=None, uid=0):
        out, seen = io.BytesIO(), []

        def gran(frame):
            seen.append(frame)
            if isinstance(answer, Exception):
                raise answer
            return answer

        code = ep.main(io.BytesIO(payload), out, whois, "unused", "unused", uid=lambda: uid, gran=gran)
        return code, out.getvalue(), seen

    def test_a_tagged_runner_gets_the_helpers_reply_and_the_frame_is_forwarded_unchanged(self):
        reply = b'gran1 11\n{"ok":true}x'
        code, out, seen = self.call(v2(RUNNER) + GOOD, reply)
        self.assertEqual((code, out, seen), (0, reply, [GOOD]))

    def test_refusals_never_reach_the_helper(self):
        for payload, uid in ((v2(OTHER) + GOOD, 0), (GOOD, 0), (v2(RUNNER) + GOOD, 1001), (v2(RUNNER) + b"gran1 0\n", 0), (v2(RUNNER) + b"gran1 4097\n", 0),
                             (v2(RUNNER) + b"gran1 01\n{}", 0), (v2(RUNNER) + b"gran1 5\nab", 0), (v2(RUNNER) + b"gran1 x\n{}", 0), (v2(RUNNER) + b"gran2 2\n{}", 0)):
            code, out, seen = self.call(payload, b"gran1 2\n{}", uid=uid)
            self.assertEqual((code, out, seen), (1, b"", []), payload[-20:])

    def test_a_helper_failure_answers_nothing(self):
        code, out, seen = self.call(v2(RUNNER) + GOOD, OSError("down"))
        self.assertEqual((code, out), (1, b""))

    def test_the_forwarder_round_trips_over_a_real_unix_socket_and_rejects_off_contract_replies(self):
        path = os.path.join(tempfile.mkdtemp(), "g.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(2)
        got = []

        def serve(reply):
            conn, _ = server.accept()
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            got.append(data)
            conn.sendall(reply)
            conn.close()

        for reply, ok in ((b'gran1 4\n{"a"}', True), (b"HTTP/1.1 200 OK\r\n\r\n", False)):
            t = threading.Thread(target=serve, args=(reply,))
            t.start()
            if ok:
                self.assertEqual(ep.gran1(GOOD, path, 5), reply)
            else:
                with self.assertRaises(ValueError):
                    ep.gran1(GOOD, path, 5)
            t.join()
        self.assertEqual(got, [GOOD, GOOD])
        server.close()

    def test_the_unit_files_match_the_contract(self):
        base = pathlib.Path(__file__).resolve().parents[2] / "lifeos" / "edge" / "granola" / "oracle"
        sock = (base / "lifeos-gran.socket").read_text()
        unit = (base / "lifeos-gran@.service").read_text()
        self.assertIn("ListenStream=/run/lifeos-gran/gran.sock", sock)
        self.assertIn("SocketUser=grans", sock)
        self.assertIn("SocketMode=0660", sock)
        self.assertIn("Accept=yes", sock)
        for line in ("User=grans", "NoNewPrivileges=yes", "StandardInput=socket", "StandardOutput=socket", "StandardError=null", "RuntimeMaxSec=85", "ProtectSystem=strict",
                     "ExecStart=/usr/bin/python3 -I /opt/lifeos-gran/gran_helper.py"):
            self.assertIn(line, unit)
        self.assertNotIn("Restart=", unit)


if __name__ == "__main__":
    unittest.main()
