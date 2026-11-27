import json
import os
import unittest
from unittest import mock

from lifeos.interview import create, granola_write as gw
from lifeos.interview.transcript import normalize_granz
from lifeos.platform.notion_client import NotionError
from tests.interview.support import FakeNotion, ROUND, UUID, envelope


class Lines(unittest.TestCase):
    def test_lines_carry_offset_speaker_and_text_in_order(self):
        out = gw.lines(envelope())
        self.assertTrue(out[0].startswith("00:00  You: Line 0"))
        self.assertTrue(out[1].startswith("00:01  Other: Line 1"))

    def test_a_long_utterance_is_one_block_of_several_runs(self):
        body = gw.blocks(envelope())
        self.assertEqual(len(body), 2 + 3)
        self.assertEqual(
            body[0]["paragraph"]["rich_text"][0]["text"]["content"], create.ACCEPTED + envelope().source_content_sha256
        )
        self.assertGreater(len(body[3]["paragraph"]["rich_text"]), 1)


class Write(unittest.TestCase):
    def test_not_live_plans_and_writes_nothing(self):
        client = FakeNotion()
        code, counts = gw.write(client, ROUND, envelope(), False)
        self.assertEqual((code, counts["blocks_planned"]), ("TRANSCRIPT_PLANNED", 5))
        self.assertFalse([c for c in client.calls if c[0] in ("POST", "PATCH")])

    def test_absent_page_is_created_and_read_back(self):
        client = FakeNotion()
        code, counts = gw.write(client, ROUND, envelope(), True)
        self.assertEqual((code, counts["readback_ok"], counts["blocks_written"]), ("TRANSCRIPT_WRITTEN", 1, 5))
        (page,) = client.templated  # made from the Full Width template
        self.assertFalse(
            [b for b in client.pages[page] if b.get("id") == f"filler-{page}"]
        )  # and the template's empty paragraph is gone

    def test_more_than_one_batch_is_appended(self):
        client = FakeNotion()
        code, counts = gw.write(client, ROUND, envelope(250), True)
        self.assertEqual((code, counts["blocks_written"]), ("TRANSCRIPT_WRITTEN", 252))
        self.assertEqual(len([c for c in client.calls if c[0] == "PATCH"]), 3)  # 252 blocks in batches of 90

    def test_a_replay_is_a_noop(self):
        client = FakeNotion()
        gw.write(client, ROUND, envelope(), True)
        before = len(client.calls)
        code, _ = gw.write(client, ROUND, envelope(), True)
        self.assertEqual(code, "TRANSCRIPT_NOOP")
        self.assertFalse([c for c in client.calls[before:] if c[0] in ("POST", "PATCH")])

    def test_a_changed_transcript_is_not_rewritten(self):
        client = FakeNotion()
        gw.write(client, ROUND, envelope(2), True)
        self.assertEqual(gw.write(client, ROUND, envelope(3), True)[0], "TRANSCRIPT_CHANGED_NOT_REWRITTEN")

    def marked(self, text):
        return [
            {
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}]},
            }
        ]

    def test_an_existing_page_that_is_not_ours_is_never_touched(self):
        client = FakeNotion(
            [{"id": "x", "type": "child_page", "child_page": {"title": "Notes"}}]
        )  # found by its machine marker, not its title
        client.pages["x"] = self.marked("v7-transcript:1;draft-not-accepted")
        self.assertEqual(gw.write(client, ROUND, envelope(), True)[0], "TRANSCRIPT_PAGE_UNVERIFIED")
        self.assertFalse([c for c in client.calls if c[0] in ("POST", "PATCH")])

    def test_a_hand_made_page_without_the_marker_is_free_form_and_not_recognised(self):
        client = FakeNotion([{"id": "x", "type": "child_page", "child_page": {"title": "Full Transcript"}}])
        client.pages["x"] = self.marked("Jim wrote this")
        code, _ = gw.write(client, ROUND, envelope(), True)
        self.assertNotEqual(code, "TRANSCRIPT_PAGE_UNVERIFIED")
        self.assertEqual(client.pages["x"], self.marked("Jim wrote this"))  # and Jim's page is never edited

    def test_two_transcript_pages_are_ambiguous(self):
        two = [{"id": f"x{i}", "type": "child_page", "child_page": {"title": "Full Transcript"}} for i in range(2)]
        client = FakeNotion(two)
        for i in range(2):
            client.pages[f"x{i}"] = self.marked("v7-transcript:1;accepted=aaaa")
        self.assertEqual(gw.write(client, ROUND, envelope(), True)[0], "TRANSCRIPT_PAGE_AMBIGUOUS")

    def test_a_failed_write_is_a_fixed_code(self):
        self.assertEqual(gw.write(FakeNotion(fail_on="POST"), ROUND, envelope(), True)[0], "TRANSCRIPT_WRITE_FAILED")

    def test_a_failed_append_archives_the_stub_and_a_missing_template_makes_no_page(self):
        class AppendFails(FakeNotion):
            def call(self, method, path, body=None):
                if method == "PATCH" and path.endswith("/children"):
                    raise NotionError("NOTION_NETWORK")
                return super().call(method, path, body)

        client = AppendFails()
        self.assertEqual(gw.write(client, ROUND, envelope(), True)[0], "TRANSCRIPT_WRITE_FAILED")
        self.assertEqual(client.archived, client.templated)  # the blank page made from the template is set aside
        none = FakeNotion()
        with mock.patch.dict(os.environ, {"LIFEOS_NOTION_FULL_WIDTH_TEMPLATE": ""}):
            self.assertEqual(gw.write(none, ROUND, envelope(), True)[0], "TRANSCRIPT_WRITE_FAILED")
        self.assertEqual(none.templated, [])

    def test_a_short_read_back_is_a_mismatch_not_a_success(self):
        code, counts = gw.write(FakeNotion(drop_last=True), ROUND, envelope(), True)
        self.assertEqual((code, counts["readback_failed"]), ("TRANSCRIPT_READBACK_MISMATCH", 1))
