"""LIFE-945 (synthetic): the private endpoint's `drive1 <days>` request and the Drive helper behind it, with fake sockets and a fake Google. No network."""
import io
import json
import os
import socket
import tempfile
import threading
import unittest

from lifeos.edge.drive import drive_helper
from lifeos.imessage import alerts_endpoint as ep
from lifeos.platform import drive_week, google_read
from tests.imessage.test_alerts import OTHER, RUNNER, v2, whois

REPLY = b'{"changed":2,"complete":true,"duplicates":0,"missing_metadata":1,"new":4,"ok":true,"unfiled":1}\n'


class Endpoint(unittest.TestCase):
    def call(self, payload, answer=REPLY, uid=0):
        out, seen = io.BytesIO(), []

        def drive(frame):
            seen.append(frame)
            if isinstance(answer, Exception):
                raise answer
            return answer

        code = ep.main(io.BytesIO(payload), out, whois, "unused", "unused", uid=lambda: uid, drive=drive)
        return code, out.getvalue(), seen

    def test_a_tagged_runner_gets_the_helpers_one_line_and_only_the_request_line_is_forwarded(self):
        self.assertEqual(self.call(v2(RUNNER) + b"drive1 7\n"), (0, REPLY, [b"drive1 7\n"]))

    def test_refusals_never_reach_the_helper(self):
        for payload, uid in ((v2(OTHER) + b"drive1 7\n", 0), (b"drive1 7\n", 0), (v2(RUNNER) + b"drive1 7\n", 1001), (v2(RUNNER) + b"drive1 0\n", 0), (v2(RUNNER) + b"drive1 15\n", 0),
                             (v2(RUNNER) + b"drive1 07\n", 0), (v2(RUNNER) + b"drive1 x\n", 0), (v2(RUNNER) + b"drive1 7 8\n", 0), (v2(RUNNER) + b"drive2 7\n", 0)):
            code, out, seen = self.call(payload, uid=uid)
            self.assertEqual((code, out, seen), (1, b"", []), payload[-14:])

    def test_a_helper_failure_answers_nothing(self):
        self.assertEqual(self.call(v2(RUNNER) + b"drive1 7\n", OSError("down"))[:2], (1, b""))

    def test_the_forwarder_round_trips_over_a_real_unix_socket_and_rejects_anything_that_is_not_one_json_line(self):
        path = os.path.join(tempfile.mkdtemp(), "d.sock")
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
                self.assertEqual(ep.drive1(b"drive1 7\n", path, 5), reply)
            else:
                with self.assertRaises(ValueError):
                    ep.drive1(b"drive1 7\n", path, 5)
            t.join()
        server.close()


class Reply:
    def __init__(self, doc):
        self.doc = doc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *_):
        return json.dumps(self.doc).encode()


def fake_google(files):
    def opener(request, timeout=0):
        url = request.full_url
        if "oauth2" in url:
            return Reply({"access_token": "t", "scope": google_read.SCOPE["drive"]})
        if "about?" in url:
            return Reply({"user": {"emailAddress": "owner@example.com"}})
        if "/files/root" in url:
            return Reply({"id": "ROOT"})
        return Reply({"files": files})
    return opener


class Helper(unittest.TestCase):
    CREDS = staticmethod(lambda account, service: {"client_id": "a", "client_secret": "b", "refresh_token": "c"} if (account, service) == ("personal", "drive") else None)

    def test_a_week_of_files_becomes_counts_only_and_no_name_id_or_link_is_in_the_reply(self):
        files = [{"createdTime": "2026-11-23T10:00:00Z", "modifiedTime": "2026-11-23T10:00:00Z", "parents": ["ROOT"], "md5Checksum": "m1", "name": "SECRET-NAME", "id": "SECRET-ID"},
                 {"createdTime": "2026-11-23T11:00:00Z", "modifiedTime": "2026-11-23T11:00:00Z", "parents": ["FOLDER"], "md5Checksum": "m1"},
                 {"createdTime": "2026-10-01T11:00:00Z", "modifiedTime": "2026-11-22T11:00:00Z", "parents": ["FOLDER"]}]
        google = google_read.Google(self.CREDS, opener=fake_google(files))
        google.tokens[("personal", "drive")] = "t"                               # the wrong-account check is covered in the google_read tests
        from datetime import datetime, timezone
        out = drive_week.summary(google, "personal", 7, now=datetime(2026, 11, 24, tzinfo=timezone.utc))
        self.assertEqual(out, {"new": 2, "changed": 1, "unfiled": 1, "duplicates": 1, "missing_metadata": 1, "complete": True})
        self.assertNotIn("SECRET", json.dumps(out))

    def test_bad_frames_and_missing_credentials_and_google_failures_give_fixed_codes_only(self):
        self.assertEqual(drive_helper.answer(b"drive1 99\n", self.CREDS), {"ok": False, "code": "DRIVE_BAD_FRAME"})
        self.assertEqual(drive_helper.answer(b"drive1 7\n", None), {"ok": False, "code": "DRIVE_NOT_CONFIGURED"})

        def boom(request, timeout=0):
            raise OSError("boom with details")
        got = drive_helper.answer(b"drive1 7\n", self.CREDS, boom)
        self.assertEqual(got, {"ok": False, "code": "DRIVE_UNAVAILABLE"})

    def test_the_credentials_come_from_the_systemd_directory_and_a_missing_file_means_not_configured(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(drive_helper.creds_from(d))
        for name in ("google_read_client_id", "google_read_client_secret", "google_read_personal_drive_refresh"):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write("x\n")
        got = drive_helper.creds_from(d)
        self.assertEqual(got("personal", "drive")["refresh_token"], "x")
        self.assertIsNone(got("bytalos", "drive"))

    def test_main_reads_one_line_and_writes_one_json_line(self):
        out = io.BytesIO()
        drive_helper.main(io.BytesIO(b"drive1 99\n"), out, {})
        self.assertEqual(json.loads(out.getvalue()), {"ok": False, "code": "DRIVE_BAD_FRAME"})
        self.assertTrue(out.getvalue().endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
