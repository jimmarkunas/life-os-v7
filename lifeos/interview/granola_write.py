"""Write ONE verified Granola transcript as the round's "Full Transcript" child page, read it back, report codes only.

The page's FIRST block is `v7-transcript:1;accepted=<sha256>` (the envelope's content hash), as
create.transcript_state reads. Absent: created. Same hash: NOOP. Another hash, or a page that is not ours: no write,
a fixed code, because an existing transcript is never rewritten. Transcript text goes only to the private Notion page."""

import hashlib
from datetime import datetime

from lifeos.platform import full_width
from lifeos.platform.notion_client import NotionError, rich_text

from . import create, notion
from .transcript import Envelope

TITLE = create.TRANSCRIPT
MAX_RUN = 1800
FIRST_BATCH = 100
FOOT = "Source: Granola transcript via LIFE OS (completeness not verified by the source)."


def _offset(segment, first):
    try:
        seconds = int((datetime.fromisoformat(segment.start_timestamp.replace("Z", "+00:00")) - first).total_seconds())
    except ValueError:
        seconds = 0
    return f"{max(seconds, 0) // 60:02d}:{max(seconds, 0) % 60:02d}"


def lines(envelope):
    """The transcript lines, in order: `mm:ss  Speaker: text` (speaker falls back to the audio channel)."""
    if not envelope.segments:
        return []
    first = datetime.fromisoformat(envelope.segments[0].start_timestamp.replace("Z", "+00:00"))
    out = []
    for s in envelope.segments:
        who = s.detected_speaker_name or {"microphone": "You", "system": "Other"}.get(s.channel, "Speaker")
        out.append(f"{_offset(s, first)}  {who}: {s.text.strip()}")
    return out


def digest(text_lines):
    return hashlib.sha256("\n".join(text_lines).encode("utf-8")).hexdigest()


def _chunks(text):
    return [text[i : i + MAX_RUN] for i in range(0, len(text), MAX_RUN)] or [""]


def _heading(text):
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": rich_text(text)}}


def _paragraph(line):
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [r for part in _chunks(line) for r in rich_text(part)]},
    }


def header_lines(header):
    """The paragraph lines of the notes and summary above the transcript; `header` is ((label, text), ...)."""
    return [line.strip() for _, text in header for line in str(text or "").splitlines() if line.strip()]


def blocks(envelope, header=()):
    """[marker, source line, header sections, "Transcript" heading when there is a header, transcript paragraphs...].

    The marker is the FIRST block. One line is always one block; the read-back compares paragraphs only."""
    marker = f"{create.ACCEPTED}{envelope.source_content_sha256}"
    out = [notion.paragraph(marker), notion.paragraph(FOOT)]
    for label, text in header:
        body = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        if body:
            out += [_heading(label), *[_paragraph(line) for line in body]]
    if header_lines(header):
        out.append(_heading("Transcript"))
    for line in lines(envelope):
        out.append(_paragraph(line))
    return out


def _page_lines(client, page_id):
    """(marker text, transcript lines, block count) as read from Notion, skipping the marker and source line."""
    found = notion.children(client, page_id, [400])
    texts = [notion.text(b) for b in found if b.get("type") == "paragraph"]
    return (texts[0] if texts else ""), texts[2:], len(found)


def write(client, round_id, envelope, live, header=()):
    """-> (code, counts).

    Codes: TRANSCRIPT_WRITTEN, _NOOP, _PLANNED (not live), _CHANGED_NOT_REWRITTEN, _PAGE_UNVERIFIED, _PAGE_AMBIGUOUS,
    _WRITE_FAILED, _READBACK_MISMATCH."""
    counts = {
        "segments": len(envelope.segments),
        "blocks_planned": 0,
        "blocks_written": 0,
        "readback_ok": 0,
        "readback_failed": 0,
    }
    want = lines(envelope)
    state, page = create.transcript_state(client, round_id)
    if state == "ambiguous":
        return "TRANSCRIPT_PAGE_AMBIGUOUS", counts
    if state == "unverified":
        return "TRANSCRIPT_PAGE_UNVERIFIED", counts
    if state == "accepted":
        marker, _, _ = _page_lines(client, page)
        return (
            ("TRANSCRIPT_NOOP", counts)
            if marker == f"{create.ACCEPTED}{envelope.source_content_sha256}"
            else ("TRANSCRIPT_CHANGED_NOT_REWRITTEN", counts)
        )
    body = blocks(envelope, header)
    counts["blocks_planned"] = len(body)
    want = header_lines(header) + want
    if not live:
        return "TRANSCRIPT_PLANNED", counts
    try:
        made = full_width.create_page(client, round_id, TITLE, body, post=client.call)["id"]
        counts["blocks_written"] = len(body)
    except (NotionError, full_width.FullWidthError):
        return "TRANSCRIPT_WRITE_FAILED", counts
    try:
        marker, got, total = _page_lines(client, made)
    except NotionError:
        return "TRANSCRIPT_READBACK_MISMATCH", counts
    if (
        marker == f"{create.ACCEPTED}{envelope.source_content_sha256}"
        and digest(got) == digest(want)
        and total == len(body)
    ):
        counts["readback_ok"] = 1
        return "TRANSCRIPT_WRITTEN", counts
    counts["readback_failed"] = 1
    return "TRANSCRIPT_READBACK_MISMATCH", counts
