import json
import unittest
from contextlib import contextmanager
from unittest import mock

from lifeos.interview import granola_match, granola_write as gw
from lifeos.interview.models import Ownership
from lifeos.sources.interview import granola_auto as ga
from tests.interview.support import FakeNotion, ROUND, UUID, envelope
from tests.kit.shared import SelfCursorDb

ENV = {"NOTION_INTERVIEW_TOKEN": "t", "HIRING_PIPELINE_PAGE_ID": "p", "IMESSAGE_ALERTS_HOST": "oracle.test"}
DAY = "2026-10-13"
SECOND = "9c3d2e1f-0a1b-4c2d-8e3f-1a2b3c4d5e6f"
MEETING = {"id": UUID, "start": "2026-10-13T17:30:00Z", "title": "Becton Dickinson follow-up", "attendees": []}
OTHER = {"id": SECOND, "start": "2026-10-14T17:30:00Z", "title": "Nothing to do with a round", "attendees": []}
ROW = granola_match.Round(ROUND, DAY, "Becton Dickinson", "Senior Technical Program Manager", "")


def oracle(meetings, caps=("purge_all", "notes"), sink=None, scope="all", notes=None, bad=None):
    """`bad`: {meeting id: Oracle's error code for `show`}; the code b"" means Oracle closed the connection with no reply."""
    sink = sink if sink is not None else []
    bad = bad or {}

    def send(host, data):
        body = json.loads(data.split(b"\n", 1)[1])
        sink.append((body["op"], body.get("scope")))
        if body["op"] == "show" and body.get("id") in bad:
            out = json.dumps({"ok": False, "code": bad[body["id"]], "nonce": body["nonce"]}).encode()
            return b"" if bad[body["id"]] == "" else b"gran1 %d\n" % len(out) + out
        shown = {"json": json.dumps({"transcript": [{"id": "s0", "document_id": body.get("id") or UUID, "text": "hello", "start_timestamp": "2026-10-13T17:30:00Z",
                                                     "end_timestamp": "2026-10-13T17:30:05Z", "source": "microphone", "is_final": True}]}), **(notes or {})}
        extra = {"sync": {"documents": 7, "transcripts": 7}, "list": {"meetings": meetings, "caps": list(caps)}, "show": shown, "purge": {"purged": 1, "scope": scope}}[body["op"]]
        out = json.dumps({"ok": True, "nonce": body["nonce"], **extra}).encode()
        return b"gran1 %d\n" % len(out) + out
    return send


class Catalog:
    """An in-memory v7_meeting_catalog behind the three helpers."""
    def __init__(self, done=()):
        self.rows = {k: "carried" for k in done}
        self.saved = []

    def done(self, cursor, keys):
        return {k for k in keys if self.rows.get(k) in ga.DONE}

    def save(self, cursor, key, day, round_key, job_key, outcome, segments, header, purged, now):
        self.rows[key] = outcome
        self.saved.append((key, day, outcome, bool(header), bool(purged), job_key))

    def failed(self, cursor, keys, since):
        return {k: int(v[-1]) for k, v in self.rows.items() if k in keys and v.startswith("read_failed_")}

    def has(self, cursor, key, outcome):
        return self.rows.get(key) == outcome


@contextmanager
def nothing():
    yield SelfCursorDb()


def go(send, client, catalog, live=True, rounds=(ROW,), limit=3, patch_meetings=True):
    """`patch_meetings`: the Meetings database (LIFE-166) is stubbed to an empty, reachable table; test_granola_clients runs the real module against its own fake."""
    stub = [mock.patch.object(ga.granola_clients, "check", lambda c: "target_ok"), mock.patch.object(ga.granola_clients, "rows_where", lambda *a: []),
            mock.patch.object(ga.granola_clients, "unfiled_row", lambda *a: "created")] if patch_meetings else []
    for patcher in stub:
        patcher.start()
    try:
        return _go(send, client, catalog, live, rounds, limit)
    finally:
        for patcher in stub:
            patcher.stop()


def _go(send, client, catalog, live, rounds, limit):
    with mock.patch.object(ga, "rounds_with_parents", lambda *a: (list(rounds), {ROUND: "parent-page"})), mock.patch.object(ga.notion, "target_check", lambda *a: "target_ok"), \
            mock.patch.object(ga.notion, "ownership", lambda *a: Ownership.MACHINE), mock.patch.object(ga.create, "link_transcript", lambda *a: "transcript_linked"), \
            mock.patch.object(ga.interview_evidence, "facts", lambda *a: ("job-key-1", None)), mock.patch.object(ga, "catalog_done", catalog.done), mock.patch.object(ga, "catalog_failed", catalog.failed), \
            mock.patch.object(ga, "catalog_save", catalog.save), mock.patch.object(ga, "catalog_has", catalog.has), mock.patch.object(ga.store, "ensure_schema", lambda c: None):
        return ga.run(limit, live, environ=ENV, send=send, client=client, connect=lambda: nothing().__enter__() if False else _conn())


class _conn:
    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False

    def cursor(self):
        return self


class Auto(unittest.TestCase):
    def test_a_matched_meeting_is_written_read_back_catalogued_and_only_then_purged_whole(self):
        seen, notion, catalog = [], FakeNotion(), Catalog()
        out = go(oracle([MEETING], sink=seen), notion, catalog)
        c = out["counts"]
        self.assertEqual((out["code"], c["carried"], c["purged"], c["left_on_oracle"], c["failed"]), ("GRAN_AUTO_DONE", 1, 1, 0, 0))
        self.assertEqual([op for op, _ in seen], ["sync", "list", "show", "purge"])
        self.assertEqual(seen[-1][1], "all")                                       # the whole meeting, not only the utterances
        self.assertEqual([(s[2], s[4], s[5]) for s in catalog.saved], [("carried", False, "job-key-1"), ("carried_purged", True, "job-key-1")])   # saved BEFORE the purge, updated after

    def test_a_carried_meeting_is_never_read_or_written_again(self):
        seen, catalog = [], Catalog(done=[ga.key_of(UUID)])
        out = go(oracle([MEETING], sink=seen), FakeNotion(), catalog)
        self.assertEqual((out["code"], out["counts"]["already"], [op for op, _ in seen]), ("GRAN_AUTO_NOTHING_NEW", 1, ["sync", "list"]))

    def test_a_meeting_with_no_round_is_left_on_oracle_untouched_and_counted(self):
        seen, catalog = [], Catalog()
        out = go(oracle([MEETING, OTHER], sink=seen), FakeNotion(), catalog)
        c = out["counts"]
        self.assertEqual((c["carried"], c["unmatched"], c["left_on_oracle"], c["purged"]), (1, 1, 1, 1))
        self.assertEqual(c["why"], {"MATCH_NONE": 1})
        self.assertEqual(sum(1 for op, _ in seen if op == "purge"), 1)
        self.assertNotIn(ga.key_of(SECOND), catalog.rows)

    def test_an_oracle_without_the_whole_meeting_purge_is_never_asked_to_purge(self):
        seen, catalog = [], Catalog()
        out = go(oracle([MEETING], caps=("notes",), sink=seen), FakeNotion(), catalog)
        c = out["counts"]
        self.assertEqual((c["carried"], c["purged"], c["left_on_oracle"], c["why"]), (1, 0, 1, {"GRAN_PURGE_ALL_UNSUPPORTED": 1}))
        self.assertNotIn("purge", [op for op, _ in seen])
        self.assertEqual(catalog.rows[ga.key_of(UUID)], "carried")                  # carried, so it is not written again, but still on Oracle

    def test_a_purge_that_does_not_confirm_the_whole_scope_is_not_counted_as_purged(self):
        catalog = Catalog()
        out = go(oracle([MEETING], scope="utterances"), FakeNotion(), catalog)
        self.assertEqual((out["counts"]["purged"], out["counts"]["why"]), (0, {"GRAN_PURGE_SCOPE_NOT_CONFIRMED": 1}))
        self.assertEqual(catalog.rows[ga.key_of(UUID)], "carried")

    def test_notes_and_summary_become_a_header_above_the_transcript_and_the_page_reads_back(self):
        notion, catalog = FakeNotion(), Catalog()
        out = go(oracle([MEETING], notes={"summary": "We agreed next steps.", "notes": "Line one\n\nLine two"}), notion, catalog)
        self.assertEqual((out["counts"]["header"], out["counts"]["failed"]), (1, 0))
        page = next(v for k, v in notion.pages.items() if k != ROUND)
        kinds = [b["type"] for b in page]
        self.assertEqual(kinds[:3], ["paragraph", "paragraph", "heading_2"])          # marker first, then the source line, then the header
        self.assertIn("heading_2", kinds[kinds.index("heading_2") + 1:])               # the "Transcript" heading sits between the header and the transcript
        self.assertTrue(catalog.saved[0][3])

    def test_a_dry_run_writes_nothing_catalogs_nothing_and_purges_nothing(self):
        seen, notion, catalog = [], FakeNotion(), Catalog()
        out = go(oracle([MEETING], sink=seen), notion, catalog, live=False)
        self.assertEqual((out["counts"]["carried"], out["counts"]["failed"], out["counts"]["why"], catalog.saved), (0, 0, {"TRANSCRIPT_PLANNED": 1}, []))
        self.assertNotIn("purge", [op for op, _ in seen])
        self.assertEqual([c for c in notion.calls if c[0] != "GET"], [])

    def test_a_catalog_row_that_cannot_be_read_back_stops_the_purge(self):
        seen, catalog = [], Catalog()
        catalog.has = lambda *a: False
        out = go(oracle([MEETING], sink=seen), FakeNotion(), catalog)
        self.assertEqual((out["counts"]["failed"], out["counts"]["purged"], out["counts"]["why"]), (1, 0, {"GRAN_CATALOG_FAILED": 1}))
        self.assertNotIn("purge", [op for op, _ in seen])

    def test_only_a_few_meetings_are_carried_per_run_and_the_rest_wait(self):
        extra = [dict(MEETING, id=f"00000000-0000-4000-8000-00000000000{i}", start=f"2026-10-13T1{i}:00:00Z") for i in range(4)]
        rows = [granola_match.Round(f"r{i}", DAY, "Becton Dickinson", "Senior Technical Program Manager", "") for i in range(4)]
        out = go(oracle(extra), FakeNotion(), Catalog(), rounds=rows, limit=1)
        self.assertLessEqual(out["counts"]["carried"], 1)


def four(bad_days=()):
    """Four meetings on four days, each with its own round; the newest is first. -> (meetings, rounds, ids newest first)."""
    names = ("Alpha", "Bravo", "Charlie", "Delta")
    meetings = [dict(MEETING, id=f"00000000-0000-4000-8000-00000000000{i}", start=f"2026-10-{10 + i}T17:30:00Z", title=f"{names[i]} Corp call") for i in range(4)]
    rounds = [granola_match.Round(f"r{i}", f"2026-10-{10 + i}", f"{names[i]} Corp", "", "") for i in range(4)]
    return meetings, rounds, [m["id"] for m in reversed(meetings)]


def notion4():
    notion = FakeNotion()
    notion.pages.update({f"r{i}": [] for i in range(4)})
    return notion


class OneBadMeeting(unittest.TestCase):
    """LIFE-651: a failed read of one meeting is that meeting's; only a helper that is down stops the run."""

    def test_one_failing_meeting_is_counted_and_the_older_ones_are_still_filed(self):
        meetings, rounds, newest_first = four()
        seen, catalog = [], Catalog()
        out = go(oracle(meetings, sink=seen, bad={newest_first[0]: "GRAN_NOT_FOUND"}), notion4(), catalog, rounds=rounds, limit=5)
        c = out["counts"]
        self.assertEqual((out["code"], c["carried"], c["failed"], c["why"]), ("GRAN_AUTO_DONE", 3, 1, {"GRAN_NOT_FOUND": 1}))
        self.assertEqual(catalog.rows[ga.key_of(newest_first[0])], "read_failed_1")
        self.assertEqual(sum(1 for op, _ in seen if op == "purge"), 3)

    def test_a_meeting_that_failed_lately_is_tried_last_and_is_given_up_after_a_few_runs(self):
        meetings, rounds, newest_first = four()
        key = ga.key_of(newest_first[0])
        seen = []
        catalog = Catalog(done=[ga.key_of(i) for i in newest_first[2:]])
        catalog.rows[key] = "read_failed_1"
        out = go(oracle(meetings, sink=seen, bad={newest_first[0]: "GRAN_TIMEOUT"}), notion4(), catalog, rounds=rounds, limit=1)
        self.assertEqual(out["counts"]["carried"], 1)                                      # the older one that reads fine goes first
        self.assertEqual([op for op, _ in seen].count("show"), 1)
        catalog = Catalog(done=[ga.key_of(i) for i in newest_first[1:]])
        catalog.rows[key] = f"read_failed_{ga.READ_TRIES}"
        seen.clear()
        out = go(oracle(meetings, sink=seen, bad={newest_first[0]: "GRAN_TIMEOUT"}), notion4(), catalog, rounds=rounds)
        self.assertEqual((out["counts"]["why"], "show" in [op for op, _ in seen]), ({"GRAN_READ_GIVEN_UP": 1}, False))

    def test_a_read_that_works_again_replaces_the_failure_record(self):
        meetings, rounds, newest_first = four()
        catalog = Catalog(done=[ga.key_of(i) for i in newest_first[1:]])
        catalog.rows[ga.key_of(newest_first[0])] = "read_failed_2"
        go(oracle(meetings), notion4(), catalog, rounds=rounds)
        self.assertEqual(catalog.rows[ga.key_of(newest_first[0])], "carried_purged")

    def test_meetings_that_keep_failing_in_a_row_stop_the_run_as_a_helper_problem(self):
        meetings, rounds, newest_first = four()
        seen = []
        out = go(oracle(meetings, sink=seen, bad={i: "GRAN_TIMEOUT" for i in newest_first[:3]}), notion4(), Catalog(), rounds=rounds, limit=5)
        self.assertEqual((out["code"], out["counts"]["carried"], [op for op, _ in seen].count("show")), ("GRAN_TIMEOUT", 0, ga.STREAK))

    def test_a_helper_that_does_not_answer_stops_the_run_at_the_first_meeting(self):
        meetings, rounds, newest_first = four()
        seen = []
        out = go(oracle(meetings, sink=seen, bad={i: "" for i in newest_first}), notion4(), Catalog(), rounds=rounds, limit=5)
        self.assertEqual((out["code"], out["counts"]["carried"], [op for op, _ in seen].count("show")), ("GRAN_NO_REPLY", 0, 1))

    def test_an_oracle_that_is_signed_out_is_not_one_meetings_problem(self):
        meetings, rounds, newest_first = four()
        out = go(oracle(meetings, bad={newest_first[0]: "GRAN_AUTH_EXPIRED"}), notion4(), Catalog(), rounds=rounds, limit=5)
        self.assertEqual((out["code"], out["counts"]["carried"]), ("GRAN_AUTH_EXPIRED", 0))

    def test_a_dry_run_records_no_failure(self):
        meetings, rounds, newest_first = four()
        catalog = Catalog()
        out = go(oracle(meetings, bad={newest_first[0]: "GRAN_OVERSIZE"}), notion4(), catalog, live=False, rounds=rounds, limit=5)
        self.assertEqual((out["counts"]["why"].get("GRAN_OVERSIZE"), catalog.saved), (1, []))


class Header(unittest.TestCase):
    def test_header_labels_are_only_added_for_text_that_exists(self):
        self.assertEqual(ga.header_of({"summary": "  ", "notes": "x"}), (("Notes", "x"),))
        self.assertEqual(ga.header_of({}), ())
        self.assertEqual(gw.header_lines((("Summary", "a\n\n b "), ("Notes", ""))), ["a", "b"])

    def test_the_meeting_key_is_a_short_hash_never_the_id(self):
        self.assertEqual(len(ga.key_of(UUID)), 16)
        self.assertNotIn(UUID[:8], ga.key_of(UUID))


if __name__ == "__main__":
    unittest.main()
