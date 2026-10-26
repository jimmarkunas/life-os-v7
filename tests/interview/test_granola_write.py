import json
import unittest

from lifeos.interview import create, granola_write as gw
from lifeos.interview.transcript import normalize_granz
from lifeos.platform.notion_client import NotionError

UUID = "3f2a1c6e-1b7a-4c1e-9d55-0a1b2c3d4e5f"
ROUND = "round-page"


def envelope(n=3):
    rows = [{"id": f"s{i}", "document_id": UUID, "text": f"Line {i} " + "x" * (2500 if i == 1 else 5), "start_timestamp": f"2026-10-13T{17 + i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z",
             "end_timestamp": f"2026-10-13T{17 + (i + 1) // 3600:02d}:{(i + 1) // 60 % 60:02d}:{(i + 1) % 60:02d}Z", "source": "microphone" if i % 2 == 0 else "system", "is_final": True} for i in range(n)]
    return normalize_granz(UUID, json.dumps({"transcript": rows}))


class FakeNotion:
    def __init__(self, existing=(), fail_on=None, drop_last=False):
        self.pages = {ROUND: list(existing)}
        self.calls, self.fail_on, self.drop_last = [], fail_on, drop_last

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if self.fail_on == method:
            raise NotionError("NOTION_NETWORK")
        if method == "POST" and path == "/pages":
            pid = f"page-{len(self.pages)}"
            self.pages[pid] = list(body["children"])
            title = body["properties"]["title"]["title"][0]["text"]["content"]
            self.pages[body["parent"]["page_id"]].append({"id": pid, "type": "child_page", "child_page": {"title": title}})
            return {"id": pid}
        if method == "PATCH" and path.endswith("/children"):
            self.pages[path.split("/")[2]].extend(body["children"])
            return {"results": []}
        if method == "GET" and "/children" in path:
            blocks = self.pages[path.split("/")[2]]
            blocks = blocks[:-1] if self.drop_last and path.split("/")[2] != ROUND else blocks
            return {"results": [dict(b, id=b.get("id", "b")) if b.get("type") == "child_page" else {"id": "b", "type": b["type"], b["type"]: b[b["type"]]} for b in blocks],
                    "has_more": False, "next_cursor": None}
        raise AssertionError((method, path))


class Lines(unittest.TestCase):
    def test_lines_carry_offset_speaker_and_text_in_order(self):
        out = gw.lines(envelope())
        self.assertTrue(out[0].startswith("00:00  You: Line 0"))
        self.assertTrue(out[1].startswith("00:01  Other: Line 1"))

    def test_a_long_utterance_is_one_block_of_several_runs(self):
        body = gw.blocks(envelope())
        self.assertEqual(len(body), 2 + 3)
        self.assertEqual(body[0]["paragraph"]["rich_text"][0]["text"]["content"], create.ACCEPTED + envelope().source_content_sha256)
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

    def test_more_than_one_batch_is_appended(self):
        client = FakeNotion()
        code, counts = gw.write(client, ROUND, envelope(250), True)
        self.assertEqual((code, counts["blocks_written"]), ("TRANSCRIPT_WRITTEN", 252))
        self.assertEqual(len([c for c in client.calls if c[0] == "PATCH"]), 2)

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

    def test_an_existing_page_that_is_not_ours_is_never_touched(self):
        client = FakeNotion([{"id": "x", "type": "child_page", "child_page": {"title": "Full Transcript"}}])
        client.pages["x"] = [{"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Jim wrote this"}, "plain_text": "Jim wrote this"}]}}]
        self.assertEqual(gw.write(client, ROUND, envelope(), True)[0], "TRANSCRIPT_PAGE_UNVERIFIED")
        self.assertFalse([c for c in client.calls if c[0] in ("POST", "PATCH")])

    def test_two_transcript_pages_are_ambiguous(self):
        two = [{"id": f"x{i}", "type": "child_page", "child_page": {"title": "Full Transcript"}} for i in range(2)]
        self.assertEqual(gw.write(FakeNotion(two), ROUND, envelope(), True)[0], "TRANSCRIPT_PAGE_AMBIGUOUS")

    def test_a_failed_write_is_a_fixed_code(self):
        self.assertEqual(gw.write(FakeNotion(fail_on="POST"), ROUND, envelope(), True)[0], "TRANSCRIPT_WRITE_FAILED")

    def test_a_short_read_back_is_a_mismatch_not_a_success(self):
        code, counts = gw.write(FakeNotion(drop_last=True), ROUND, envelope(), True)
        self.assertEqual((code, counts["readback_failed"]), ("TRANSCRIPT_READBACK_MISMATCH", 1))
