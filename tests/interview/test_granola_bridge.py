import json
import unittest
from unittest import mock

from lifeos.interview import granola_bridge as gb, granola_match, granola_oracle as go
from lifeos.interview.models import Ownership
from tests.interview.test_granola_write import FakeNotion, ROUND, UUID, envelope

ENV = {"NOTION_INTERVIEW_TOKEN": "t", "HIRING_PIPELINE_PAGE_ID": "p", "IMESSAGE_ALERTS_HOST": "oracle.test"}
DAY = "2026-10-13"


def reply(request, **fields):
    op, nonce = json.loads(request.split(b"\n", 1)[1])["op"], json.loads(request.split(b"\n", 1)[1])["nonce"]
    body = json.dumps({"ok": True, "nonce": nonce, **fields}).encode()
    return b"gran1 %d\n" % len(body) + body, op


def oracle(meetings, transcript=None, fail=None, sink=None):
    """A fake `gran1` server: answers by op, records the ops it saw."""
    sink = sink if sink is not None else []
    shown = transcript if transcript is not None else json.dumps({"transcript": [
        {"id": "s0", "document_id": UUID, "text": "hello", "start_timestamp": "2026-10-13T17:30:00Z", "end_timestamp": "2026-10-13T17:30:05Z", "source": "microphone", "is_final": True}]})

    def send(host, data):
        op = json.loads(data.split(b"\n", 1)[1])["op"]
        sink.append(op)
        if fail and fail[0] == op:
            nonce = json.loads(data.split(b"\n", 1)[1])["nonce"]
            body = json.dumps({"ok": False, "code": fail[1], "nonce": nonce}).encode()
            return b"gran1 %d\n" % len(body) + body
        extra = {"sync": {"documents": 7, "transcripts": 7}, "list": {"meetings": meetings}, "show": {"json": shown}, "purge": {"purged": 1}}[op]
        return reply(data, **extra)[0]
    return send


MEETING = {"id": UUID, "start": "2026-10-13T17:30:00Z", "title": "Becton Dickinson follow-up", "attendees": []}
ROW = granola_match.Round(ROUND, DAY, "Becton Dickinson", "Senior Technical Program Manager", "")


def go_run(send, client, live=True, rounds=(ROW,), only_day=DAY):
    with mock.patch.object(gb, "rounds", lambda *a: list(rounds)), mock.patch.object(gb.notion, "target_check", lambda *a: "target_ok"), \
            mock.patch.object(gb.notion, "ownership", lambda *a: Ownership.MACHINE), mock.patch.object(gb.create, "link_transcript", lambda *a: "transcript_linked"):
        return gb.run(live, only_day, environ=ENV, send=send, client=client)


class Client(unittest.TestCase):
    def test_the_frame_carries_op_and_nonce_under_the_verb(self):
        data = go.frame("show", "n1", id="x")
        self.assertTrue(data.startswith(b"gran1 "))
        self.assertEqual(json.loads(data.split(b"\n", 1)[1]), {"op": "show", "nonce": "n1", "id": "x"})

    def test_an_unknown_op_is_refused(self):
        with self.assertRaises(go.OracleError) as ctx:
            go.frame("shell", "n")
        self.assertEqual(ctx.exception.code, "GRAN_BAD_OP")

    def test_no_host_no_reply_and_a_bad_length_are_fixed_codes(self):
        for host, raw, code in (("", b"x", "GRAN_NO_HOST"), ("h", b"", "GRAN_NO_REPLY"), ("h", b"gran1 5\n{}", "GRAN_REPLY_INVALID")):
            with self.assertRaises(go.OracleError) as ctx:
                go.call(host, "sync", send=lambda h, d, raw=raw: raw)
            self.assertEqual(ctx.exception.code, code)

    def test_a_reply_that_does_not_echo_the_nonce_is_refused(self):
        body = json.dumps({"ok": True, "nonce": "other"}).encode()
        with self.assertRaises(go.OracleError) as ctx:
            go.call("h", "sync", send=lambda h, d: b"gran1 %d\n" % len(body) + body)
        self.assertEqual(ctx.exception.code, "GRAN_NONCE_MISMATCH")

    def test_oracles_known_code_passes_through_and_an_unknown_one_does_not(self):
        for given, expect in (("GRAN_AUTH_EXPIRED", "GRAN_AUTH_EXPIRED"), ("SOMETHING_ELSE", "GRAN_REPLY_INVALID")):
            def send(host, data, given=given):
                nonce = json.loads(data.split(b"\n", 1)[1])["nonce"]
                body = json.dumps({"ok": False, "code": given, "nonce": nonce}).encode()
                return b"gran1 %d\n" % len(body) + body
            with self.assertRaises(go.OracleError) as ctx:
                go.call("h", "sync", send=send)
            self.assertEqual(ctx.exception.code, expect)


class Bridge(unittest.TestCase):
    def test_the_happy_path_writes_reads_back_then_purges_once(self):
        seen, notion = [], FakeNotion()
        out = go_run(oracle([MEETING], sink=seen), notion)
        self.assertEqual((out["code"], out["counts"]["readback_ok"], out["counts"]["purged"], out["counts"]["linked"]), ("TRANSCRIPT_WRITTEN", 1, 1, 1))
        self.assertEqual(seen, ["sync", "list", "show", "purge"])

    def test_a_replay_finds_the_same_page_and_still_purges(self):
        notion = FakeNotion()
        go_run(oracle([MEETING]), notion)
        seen = []
        out = go_run(oracle([MEETING], sink=seen), notion)
        self.assertEqual((out["code"], out["counts"]["purged"]), ("TRANSCRIPT_NOOP", 1))
        self.assertEqual(len([c for c in notion.calls if c[0] == "POST"]), 1)

    def test_a_dry_run_plans_and_never_purges(self):
        seen = []
        out = go_run(oracle([MEETING], sink=seen), FakeNotion(), live=False)
        self.assertEqual(out["code"], "TRANSCRIPT_PLANNED")
        self.assertNotIn("purge", seen)

    def test_zero_or_two_meetings_on_the_pinned_day_stops_before_any_fetch(self):
        for meetings in ([], [MEETING, dict(MEETING, id="2f2a1c6e-1b7a-4c1e-9d55-0a1b2c3d4e5f", start="2026-10-13T19:00:00Z")]):
            seen = []
            out = go_run(oracle(meetings, sink=seen), FakeNotion())
            self.assertEqual(out["code"], "GRAN_PIN_NOT_ONE_MEETING")
            self.assertNotIn("show", seen)

    def test_no_matching_round_is_a_fixed_code_and_nothing_is_fetched(self):
        seen = []
        out = go_run(oracle([MEETING], sink=seen), FakeNotion(), rounds=())
        self.assertEqual(out["code"], "MATCH_NONE")
        self.assertEqual(seen, ["sync", "list"])

    def test_a_contract_failure_writes_and_purges_nothing(self):
        seen, notion = [], FakeNotion()
        out = go_run(oracle([MEETING], transcript=json.dumps({"transcript": []}), sink=seen), notion)
        self.assertEqual(out["code"], "TRANSCRIPT_NO_TRANSCRIPT_SEGMENTS")
        self.assertNotIn("purge", seen)
        self.assertFalse([c for c in notion.calls if c[0] in ("POST", "PATCH")])

    def test_an_oracle_refusal_stops_with_its_code(self):
        out = go_run(oracle([MEETING], fail=("sync", "GRAN_AUTH_EXPIRED")), FakeNotion())
        self.assertEqual(out["code"], "GRAN_AUTH_EXPIRED")

    def test_a_readback_mismatch_never_purges(self):
        seen = []
        out = go_run(oracle([MEETING], sink=seen), FakeNotion(drop_last=True))
        self.assertEqual(out["code"], "TRANSCRIPT_READBACK_MISMATCH")
        self.assertNotIn("purge", seen)

    def test_the_pin_is_required_and_must_be_a_real_day(self):
        for bad in ("", None, "2026-13-40", "yesterday"):
            self.assertEqual(go_run(oracle([MEETING]), FakeNotion(), only_day=bad)["code"], "GRAN_PIN_INVALID")

    def test_counts_never_carry_text(self):
        out = go_run(oracle([MEETING]), FakeNotion())
        self.assertNotIn("Becton", json.dumps(out))
        self.assertNotIn("hello", json.dumps(out))


class Stage(unittest.TestCase):
    def test_the_stage_reports_the_code_and_counts_and_fails_on_any_stop(self):
        with mock.patch.object(gb, "run", lambda live, day, environ=None: {"code": "MATCH_NONE", "counts": {"meetings": 7}}):
            self.assertEqual(gb.stage(1, True, environ={"GRAN_PIN_DAY": " 2026-10-13 "}), {"code": "MATCH_NONE", "failed": 1, "meetings": 7})
        with mock.patch.object(gb, "run", lambda live, day, environ=None: {"code": "TRANSCRIPT_WRITTEN", "counts": {}}):
            self.assertEqual(gb.stage(1, True, environ={})["failed"], 0)

    def test_the_stage_is_registered(self):
        from lifeos import run
        self.assertIn("granola", run.STAGES)
