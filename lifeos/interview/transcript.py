"""Contract for one Granola transcript as `granz show <uuid> --transcript --json` returns it; validates and hashes it.

Pure: no network or files. Failure codes are TRANSCRIPT_* (lifeos/platform/codes.py). Input is `{"transcript":
[...]}`."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
import uuid
from typing import Any

SCHEMA_VERSION = "interview.transcript.v1"
MAX_SEGMENTS = 30_000
MAX_TEXT_CHARS = 12_000
MAX_BYTES = 25_000_000
CHANNELS = {"microphone", "system", "unknown"}


class ContractError(ValueError):

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _parse_time(value: Any, required: bool) -> datetime | None:
    if value is None or value == "":
        if required:
            raise ContractError("TRANSCRIPT_MISSING_TIMESTAMP")
        return None
    if not isinstance(value, str) or len(value) > 48:
        raise ContractError("TRANSCRIPT_INVALID_TIMESTAMP")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("TRANSCRIPT_INVALID_TIMESTAMP") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ContractError("TRANSCRIPT_TIMEZONE_REQUIRED")
    return result


def _source_uuid(value: str) -> str:
    try:
        if not isinstance(value, str) or not re.fullmatch(
            r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", value
        ):
            raise ValueError("invalid")
        return str(uuid.UUID(value))
    except (ValueError, TypeError) as exc:
        raise ContractError("TRANSCRIPT_INVALID_DOCUMENT_UUID") from exc


@dataclass(frozen=True)
class Segment:
    ordinal: int
    source_segment_id: str | None
    start_timestamp: str
    end_timestamp: str | None
    text: str
    channel: str
    detected_speaker_name: str | None
    is_final: bool | None


@dataclass(frozen=True)
class Envelope:
    schema_version: str
    source_key: str
    transcript_status: str
    source_fidelity: str
    segments: tuple[Segment, ...]
    source_content_sha256: str

    def public_receipt(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "transcript_status": self.transcript_status,
            "segment_count": len(self.segments),
            "source_fidelity": self.source_fidelity,
        }


def _refuse(bad, code):
    if bad:
        raise ContractError(code)


def normalize_granz(document_uuid: str, raw_json: str | bytes) -> Envelope:
    """Validate the JSON of granz `show <uuid> --transcript --json` into an Envelope; fails closed (ContractError) on
    malformed, unfinal or unsorted data. The CLI gives no completeness proof, so "available" means segments were
    retrieved, not full audio fidelity.
    """
    uid = _source_uuid(document_uuid)
    _refuse(not isinstance(raw_json, (str, bytes)) or len(raw_json) > MAX_BYTES, "TRANSCRIPT_PAYLOAD_TOO_LARGE_OR_INVALID")
    try:
        obj = json.loads(raw_json)
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        raise ContractError("TRANSCRIPT_INVALID_JSON") from exc
    _refuse(not isinstance(obj, dict) or set(obj) != {"transcript"}, "TRANSCRIPT_UNEXPECTED_CLI_SHAPE")
    rows = obj["transcript"]
    _refuse(not isinstance(rows, list) or not rows, "TRANSCRIPT_NO_TRANSCRIPT_SEGMENTS")
    _refuse(len(rows) > MAX_SEGMENTS, "TRANSCRIPT_TOO_MANY_SEGMENTS")

    result: list[Segment] = []
    seen_ids: set[str] = set()
    prev_time: datetime | None = None
    for idx, item in enumerate(rows):
        _refuse(not isinstance(item, dict), "TRANSCRIPT_INVALID_SEGMENT")
        source_document = item.get("document_id")
        _refuse(source_document is not None and _source_uuid(source_document) != uid, "TRANSCRIPT_SEGMENT_DOCUMENT_MISMATCH")
        text = item.get("text")
        _refuse(not isinstance(text, str) or not text.strip(), "TRANSCRIPT_EMPTY_SEGMENT_TEXT")
        _refuse(len(text) > MAX_TEXT_CHARS, "TRANSCRIPT_SEGMENT_TEXT_TOO_LONG")
        start_dt = _parse_time(item.get("start_timestamp"), required=True)
        end_dt = _parse_time(item.get("end_timestamp"), required=False)
        assert start_dt is not None
        _refuse(prev_time is not None and start_dt < prev_time, "TRANSCRIPT_NONMONOTONIC_SEGMENTS")
        _refuse(end_dt is not None and end_dt < start_dt, "TRANSCRIPT_END_PRECEDES_START")
        prev_time = start_dt

        raw_id = item.get("id")
        if raw_id is not None:
            _refuse(not isinstance(raw_id, str) or not raw_id or len(raw_id) > 256, "TRANSCRIPT_INVALID_SEGMENT_ID")
            _refuse(raw_id in seen_ids, "TRANSCRIPT_DUPLICATE_SEGMENT_ID")
            seen_ids.add(raw_id)

        raw_channel = item.get("source")
        channel = "unknown" if raw_channel is None else raw_channel
        _refuse(not isinstance(channel, str) or channel not in CHANNELS, "TRANSCRIPT_INVALID_CHANNEL")
        speaker = item.get("detected_speaker_name")
        _refuse(speaker is not None and (not isinstance(speaker, str) or not speaker.strip() or len(speaker) > 256), "TRANSCRIPT_INVALID_SPEAKER_LABEL")
        final = item.get("is_final")
        _refuse(final is not None and not isinstance(final, bool), "TRANSCRIPT_INVALID_IS_FINAL")
        _refuse(final is False, "TRANSCRIPT_UNFINALIZED_SEGMENT")
        result.append(Segment(
            ordinal=idx,
            source_segment_id=raw_id,
            start_timestamp=item["start_timestamp"],
            end_timestamp=item.get("end_timestamp"),
            text=text,
            channel=channel,
            detected_speaker_name=speaker,
            is_final=final,
        ))

    # hash only the ordered, normalized source facts, so a replay hashes identically
    canonical = json.dumps(
        [asdict(s) for s in result],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return Envelope(
        schema_version=SCHEMA_VERSION,
        source_key=f"granola:{uid}",
        transcript_status="available_unverified_completeness",
        source_fidelity="granz_private_desktop_api",
        segments=tuple(result),
        source_content_sha256=sha256(canonical).hexdigest(),
    )
