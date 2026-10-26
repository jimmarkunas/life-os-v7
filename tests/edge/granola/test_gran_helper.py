import io
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest

from lifeos.edge.granola import gran_helper as gh
from lifeos.interview import transcript as contract

DOC = "11111111-2222-4333-8444-555555555555"
OTHER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
SECRET = "SECRET-transcript-words"


def raw_transcript(doc, n=3):
    rows = [{"id": "u%d" % i, "document_id": doc, "text": "%s %d" % (SECRET, i), "start_timestamp": "2026-10-13T17:%02d:00Z" % (30 + i),
             "end_timestamp": "2026-10-13T17:%02d:30Z" % (30 + i), "source": "microphone", "is_final": True} for i in range(n)]
    return json.dumps({"transcript": rows})


def frame(body):
    data = json.dumps(body).encode()
    return b"gran1 %d\n" % len(data) + data


class Fake:
    """A stand-in for the grans CLI: records every command and answers like it."""
    def __init__(self, signed_in=True, sync_exit=0, hang=False, show_out=None):
        self.calls, self.signed_in, self.sync_exit, self.hang, self.show_out = [], signed_in, sync_exit, hang, show_out

    def __call__(self, argv, capture_output=True, timeout=None, stdin=None):
        self.calls.append(argv[1:])
        if self.hang:
            raise subprocess.TimeoutExpired(argv, timeout)
        head = argv[1]
        if head == "sync":
            return subprocess.CompletedProcess(argv, self.sync_exit, b"", b"boom words that must never be returned")
        if head == "auth":
            return subprocess.CompletedProcess(argv, 0 if self.signed_in else 1, b"Signed in.\n" if self.signed_in else b"Not signed in\n", b"")
        if head == "show":
            out = self.show_out if self.show_out is not None else raw_transcript(argv[2])
            return subprocess.CompletedProcess(argv, 0, out.encode() if isinstance(out, str) else out, b"")
        raise AssertionError(argv)


class Helper(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "grans.db")
        db = sqlite3.connect(self.db)
        db.executescript("""create table documents(id text primary key, created_at text, deleted_at text, google_calendar_event_json text, title text);
            create table document_people(document_id text, email text, full_name text);
            create table transcript_utterances(id text primary key, document_id text, text text);""")
        db.execute("insert into documents values (?,?,?,?,?)", (DOC, "2026-10-13T17:31:00Z", None, json.dumps({"start": {"dateTime": "2026-10-13T12:30:00-05:00"}}), "Follow up sync"))
        db.execute("insert into documents values (?,?,?,?,?)", (OTHER, "2026-10-20T15:00:00Z", None, None, None))
        db.execute("insert into documents values (?,?,?,?,?)", ("deleted-doc-0001", "2026-10-01T15:00:00Z", "2026-10-02T00:00:00Z", None, "gone"))
        db.executemany("insert into document_people values (?,?,?)", [(DOC, "attendee@example.com", "A"), (DOC, None, "Named Only"), (DOC, None, None)])
        for doc in (DOC, OTHER):
            db.executemany("insert into transcript_utterances values (?,?,?)", [("%s-%d" % (doc[:4], i), doc, "t") for i in range(3)])
        db.commit()
        db.close()

    def call(self, body, fake=None, raw=None):
        fake = fake or Fake()
        out = io.BytesIO()
        gh.main(io.BytesIO(raw if raw is not None else frame(body)), out, gh.Context(run=fake, grans="/g", db=self.db, contract=contract))
        head, _, rest = out.getvalue().partition(b"\n")
        self.assertEqual(head, b"gran1 %d" % len(rest))
        return json.loads(rest), fake

    def utterances(self, doc):
        db = sqlite3.connect(self.db)
        n = db.execute("select count(*) from transcript_utterances where document_id=?", (doc,)).fetchone()[0]
        db.close()
        return n

    def test_sync_runs_documents_then_transcripts_and_reports_counts_from_the_database(self):
        reply, fake = self.call({"op": "sync", "nonce": "n1"})
        self.assertEqual(fake.calls, [["sync", "documents"], ["sync", "transcripts"]])
        self.assertEqual(reply, {"ok": True, "code": "OK", "nonce": "n1", "documents": 2, "transcripts": 2, "exit": 0})

    def test_a_failed_sync_is_a_fixed_code_and_never_the_cli_output(self):
        reply, _ = self.call({"op": "sync", "nonce": "n"}, Fake(sync_exit=1))
        self.assertEqual((reply["ok"], reply["code"]), (False, "GRAN_SYNC_FAILED"))
        self.assertNotIn("boom", json.dumps(reply))

    def test_a_failed_sync_while_signed_out_is_auth_expired(self):
        self.assertEqual(self.call({"op": "sync", "nonce": "n"}, Fake(sync_exit=1, signed_in=False))[0]["code"], "GRAN_AUTH_EXPIRED")

    def test_a_hung_cli_is_a_timeout(self):
        self.assertEqual(self.call({"op": "sync", "nonce": "n"}, Fake(hang=True))[0]["code"], "GRAN_TIMEOUT")

    def test_list_returns_id_start_title_attendees_for_live_meetings_only(self):
        reply, fake = self.call({"op": "list", "nonce": "n"})
        self.assertEqual(fake.calls, [])                                    # read from the database; the CLI is not run
        ids = [m["id"] for m in reply["meetings"]]
        self.assertEqual(ids, [DOC, OTHER])                                 # oldest first; the deleted meeting is absent
        self.assertEqual(reply["meetings"][0]["start"], "2026-10-13T12:30:00-05:00")        # the calendar start wins over created_at
        self.assertEqual(reply["meetings"][0]["attendees"], ["attendee@example.com", "Named Only"])
        self.assertEqual(reply["meetings"][1]["start"], "2026-10-20T15:00:00Z")             # no calendar event: created_at

    def test_show_returns_the_cli_json_verbatim(self):
        reply, fake = self.call({"op": "show", "id": DOC, "nonce": "n"})
        self.assertEqual(fake.calls, [["show", DOC, "--transcript", "--json", "--no-color"]])
        self.assertEqual(reply["json"], raw_transcript(DOC))

    def test_show_of_an_unknown_or_deleted_meeting_is_not_found(self):
        for doc in ("99999999-9999-4999-8999-999999999999", "deleted-doc-0001"):
            self.assertEqual(self.call({"op": "show", "id": doc, "nonce": "n"})[0]["code"], "GRAN_NOT_FOUND")

    def test_show_over_the_contract_limit_is_oversize(self):
        reply, _ = self.call({"op": "show", "id": DOC, "nonce": "n"}, Fake(show_out=b"x" * (gh.MAX_SHOW + 1)))
        self.assertEqual(reply["code"], "GRAN_OVERSIZE")

    def test_purge_deletes_only_that_meetings_utterances_when_the_hash_matches(self):
        want = contract.normalize_granz(DOC, raw_transcript(DOC)).source_content_sha256
        reply, _ = self.call({"op": "purge", "id": DOC, "content_sha256": want, "nonce": "n"})
        self.assertEqual(reply, {"ok": True, "code": "OK", "nonce": "n", "purged": 1})
        self.assertEqual((self.utterances(DOC), self.utterances(OTHER)), (0, 3))
        again, _ = self.call({"op": "purge", "id": DOC, "content_sha256": want, "nonce": "n"})
        self.assertEqual(again["purged"], 0)                                # nothing left, never an error

    def test_purge_with_the_wrong_hash_deletes_nothing(self):
        reply, _ = self.call({"op": "purge", "id": DOC, "content_sha256": "0" * 64, "nonce": "n"})
        self.assertEqual((reply["ok"], reply["code"]), (False, "GRAN_HASH_MISMATCH"))
        self.assertEqual(self.utterances(DOC), 3)

    def test_purge_of_a_transcript_that_fails_the_contract_returns_the_contract_code_and_deletes_nothing(self):
        reply, _ = self.call({"op": "purge", "id": DOC, "content_sha256": "0" * 64, "nonce": "n"}, Fake(show_out="not json"))
        self.assertEqual(reply["code"], "TRANSCRIPT_INVALID_JSON")
        self.assertEqual(self.utterances(DOC), 3)

    def test_bad_frames_and_ops_are_fixed_codes(self):
        cases = [({"op": "show", "nonce": "n"}, "GRAN_BAD_FRAME"),                         # no id
                 ({"op": "show", "id": "../../etc/passwd", "nonce": "n"}, "GRAN_BAD_FRAME"),
                 ({"op": "show", "id": DOC + "; id", "nonce": "n"}, "GRAN_BAD_FRAME"),
                 ({"op": "purge", "id": DOC, "content_sha256": "ABC", "nonce": "n"}, "GRAN_BAD_FRAME"),
                 ({"op": "purge", "id": DOC, "nonce": "n"}, "GRAN_BAD_FRAME"),
                 ({"op": "delete", "nonce": "n"}, "GRAN_BAD_OP"),
                 ({"nonce": "n"}, "GRAN_BAD_OP"),
                 ({"op": "list"}, "GRAN_BAD_FRAME"),                                        # no nonce
                 ({"op": "list", "nonce": "x" * 65}, "GRAN_BAD_FRAME")]
        for body, code in cases:
            reply, fake = self.call(body)
            self.assertEqual((reply["ok"], reply["code"]), (False, code), body)
            self.assertEqual(fake.calls, [], body)                                          # nothing was run
        for raw in (b"", b"gran1 5\nabc", b"gran1 0\n", b"gran1 4097\n", b"gran2 2\n{}", b"gran1 2\n[]", b"gran1 7\nnot json", b"gran1 a\n{}", b"gran1 01\n{}"):
            reply, fake = self.call(None, raw=raw)
            self.assertEqual(reply["code"], "GRAN_BAD_FRAME", raw)

    def test_every_reply_code_is_registered(self):
        from lifeos.platform import codes
        for code in ("GRAN_BAD_FRAME", "GRAN_BAD_OP", "GRAN_NOT_FOUND", "GRAN_SYNC_FAILED", "GRAN_AUTH_EXPIRED", "GRAN_TIMEOUT", "GRAN_OVERSIZE", "GRAN_HASH_MISMATCH", "GRAN_OFF_CONTRACT"):
            self.assertIn(code, codes.CODES)


if __name__ == "__main__":
    unittest.main()
