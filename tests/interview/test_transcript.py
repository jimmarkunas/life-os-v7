"""LIFE-37: the Granola transcript contract (lifeos/interview/transcript.py), synthetic data only; no live data or credentials."""
import json
import unittest
from lifeos.interview.transcript import ContractError, normalize_granz, disposition

DOC = "00000000-0000-4000-8000-000000000001"


def segment(**update):
    row = {
        "id": "segment-synthetic-1",
        "document_id": DOC,
        "start_timestamp": "2026-10-21T13:00:00-05:00",
        "end_timestamp": "2026-10-21T13:00:02-05:00",
        "text": "Synthetic hello from a fictional speaker.",
        "source": "system",
        "is_final": True,
        "detected_speaker_name": "Synthetic Speaker",
    }
    row.update(update)
    return row


def wrapper(rows):
    return json.dumps({"transcript": rows})


class ContractTests(unittest.TestCase):
    def assert_fails(self, code, data, doc=DOC):
        with self.assertRaises(ContractError) as x:
            normalize_granz(doc, data)
        self.assertEqual(code, x.exception.code)
        self.assertNotIn("Synthetic Speaker", str(x.exception))

    def test_success_structure_and_speaker_vs_channel(self):
        e = normalize_granz(DOC, wrapper([segment()]))
        self.assertEqual("system", e.segments[0].channel)
        self.assertEqual("Synthetic Speaker", e.segments[0].detected_speaker_name)
        self.assertEqual("granola:" + DOC, e.source_key)
        self.assertEqual(1, e.public_receipt()["segment_count"])
        self.assertNotIn("source_key", e.public_receipt())
        self.assertNotIn("segments", e.public_receipt())

    def test_unknown_speaker_is_not_invented(self):
        e = normalize_granz(DOC, wrapper([segment(detected_speaker_name=None, source="microphone")]))
        self.assertIsNone(e.segments[0].detected_speaker_name)
        self.assertEqual("microphone", e.segments[0].channel)

    def test_replay_determinism_and_changed_content(self):
        a = normalize_granz(DOC, wrapper([segment()]))
        b = normalize_granz(DOC, wrapper([segment()]))
        c = normalize_granz(DOC, wrapper([segment(text="New synthetic content")]))
        self.assertEqual("INSERT", disposition(None, a.source_content_sha256))
        self.assertEqual("NOOP", disposition(a.source_content_sha256, b.source_content_sha256))
        self.assertEqual("UPDATE", disposition(a.source_content_sha256, c.source_content_sha256))

    def test_timestamp_z_and_offset(self):
        e = normalize_granz(DOC, wrapper([segment(start_timestamp="2026-10-21T18:00:00Z",end_timestamp="2026-10-21T18:00:01Z")]))
        self.assertTrue(e.segments[0].start_timestamp.endswith("Z"))

    def test_timestamp_different_offsets_still_monotonic(self):
        a = segment(end_timestamp="2026-10-21T13:00:01-05:00")
        b = segment(id="segment-synthetic-2", start_timestamp="2026-10-21T18:00:02Z", end_timestamp="2026-10-21T18:00:03Z")
        self.assertEqual(2,len(normalize_granz(DOC, wrapper([a,b])).segments))

    def test_bad_uuid(self):
        self.assert_fails("TRANSCRIPT_INVALID_DOCUMENT_UUID",wrapper([segment()]),doc="../../../etc/passwd")

    def test_invalid_json(self):
        self.assert_fails("TRANSCRIPT_INVALID_JSON", "wrong")

    def test_empty_transcript(self):
        self.assert_fails("TRANSCRIPT_NO_TRANSCRIPT_SEGMENTS",wrapper([]))

    def test_unexpected_json_shape(self):
        self.assert_fails("TRANSCRIPT_UNEXPECTED_CLI_SHAPE",json.dumps({"summary": "synthetic"}))

    def test_mismatch_document(self):
        self.assert_fails("TRANSCRIPT_SEGMENT_DOCUMENT_MISMATCH",wrapper([segment(document_id="00000000-0000-4000-8000-000000000002")]))

    def test_empty_text(self):
        self.assert_fails("TRANSCRIPT_EMPTY_SEGMENT_TEXT",wrapper([segment(text="  ")]))

    def test_unfinalized(self):
        self.assert_fails("TRANSCRIPT_UNFINALIZED_SEGMENT",wrapper([segment(is_final=False)]))

    def test_bad_source(self):
        self.assert_fails("TRANSCRIPT_INVALID_CHANNEL",wrapper([segment(source="evil")]))

    def test_duplicate_segment_id(self):
        a=segment()
        b=segment(start_timestamp="2026-10-21T13:00:03-05:00",end_timestamp="2026-10-21T13:00:05-05:00")
        self.assert_fails("TRANSCRIPT_DUPLICATE_SEGMENT_ID",wrapper([a,b]))

    def test_backward_timestamps(self):
        a=segment()
        b=segment(id="synthetic2", start_timestamp="2026-10-21T12:59:59-05:00")
        self.assert_fails("TRANSCRIPT_NONMONOTONIC_SEGMENTS",wrapper([a,b]))

    def test_bad_end(self):
        self.assert_fails("TRANSCRIPT_END_PRECEDES_START",wrapper([segment(end_timestamp="2026-10-21T12:59:59-05:00")]))

    def test_timezone_missing(self):
        self.assert_fails("TRANSCRIPT_TIMEZONE_REQUIRED",wrapper([segment(start_timestamp="2026-10-21T13:00:00")]))

    def test_none_channel_ok_unknown(self):
        e=normalize_granz(DOC,wrapper([segment(source=None)]))
        self.assertEqual("unknown",e.segments[0].channel)

    def test_digest_validation(self):
        with self.assertRaises(ContractError):
            disposition("strange", "00"*32)

if __name__ == "__main__":
    unittest.main()
