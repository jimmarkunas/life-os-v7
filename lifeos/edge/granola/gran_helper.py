#!/usr/bin/python3 -I
"""LIFE-37 Step 2 (Jim's approval Oct 26 2026; call shape agreed with Career Claude in Jira comments 11235 and 11242): the Granola transcript helper on Oracle.

systemd starts one process per connection as the `grans` user (socket activation, Accept=yes: the connection is stdin/stdout). The only caller is the private
endpoint (lifeos/imessage/alerts_endpoint.py), which has already checked Tailscale's PROXY header and that the caller carries tag:v7-runner. One request per
connection: `gran1 <len>\\n` + a JSON object of 1..4096 bytes; one reply: `gran1 <len>\\n` + a JSON object `{"ok", "code", "nonce", ...}`.
Four fixed operations, no shell and nothing the caller can choose beyond a meeting id and a hash:
  sync   `grans sync documents`, then `grans sync transcripts` (no date limit). Reply {"documents": n, "transcripts": n, "exit": 0}, counts read from the local database.
  list   every live meeting: {"id", "start" (the calendar start when the meeting has one, else when Granola created it, ISO with zone), "title", "attendees"}, plus
         "caps": what this helper can do beyond the base verbs ("notes", "purge_all"; LIFE-122 comment 11269).
         Private fields travel to the one caller in memory; nothing is logged, written or kept here.
  show   {"id"} -> `grans show <id> --transcript --json --no-color` stdout verbatim in "json" (at most 25,000,000 bytes: the contract's own limit), plus "notes" and
         "summary" (Granola's own, each at most 60,000 characters, "" when there is none).
  purge  {"id", "content_sha256"} -> deletes that one meeting's transcript utterances from grans.db, but only if the caller's hash equals the hash this process computes
         itself with the transcript contract (lifeos/interview/transcript.py, installed beside this file) over a fresh `grans show` of that meeting. The caller sends
         it after it has written the transcript to Notion and read it back. Reply {"purged": 0|1}. Titles, notes and attendees stay (they are what `list` matches on).
  purge  {"id", "content_sha256", "scope": "all"} -> the same hash check, then deletes EVERYTHING held for that meeting: utterances, panels, text chunks and their vectors,
         sync-log rows, attendees, its calendar event, and the meeting row itself (notes, summary, title). Reply {"purged": n >= 1 on first success, "scope": "all"}.
         Idempotent: a meeting that is already gone answers {"purged": 0, "scope": "all"}. Granola's next `sync` can bring a purged meeting back; the caller's catalog stops it
         being carried twice. Any other scope value is GRAN_BAD_FRAME.
Fixed error codes (reply ok false): GRAN_BAD_FRAME, GRAN_BAD_OP, GRAN_NOT_FOUND, GRAN_SYNC_FAILED, GRAN_AUTH_EXPIRED, GRAN_TIMEOUT, GRAN_OVERSIZE, GRAN_HASH_MISMATCH, and
the contract's own TRANSCRIPT_* code when `purge` meets a transcript that no longer passes it. Never a message, path, exception text or transcript content.
The Mac and Oracle share one Granola session, so a refresh on the Mac can sign Oracle out: that is GRAN_AUTH_EXPIRED, and a person signs Oracle in again. Stdlib only."""
import json
import os
import re
import sqlite3
import subprocess
import sys

GRANS = "/home/grans/bin/grans"
DB = "/home/grans/.local/share/grans/grans.db"
CONTRACT_DIR = "/opt/lifeos-gran"
MAX_FRAME = 4096
MAX_SHOW = 25_000_000
MAX_REPLY = MAX_SHOW + 4096
SYNC_EACH, SHOW_WAIT, SHORT_WAIT = 35, 50, 20          # seconds; the endpoint's hard limit is 90 for the whole connection
ID = re.compile(r"^[0-9A-Za-z_-]{8,64}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_TEXT = 60_000                                       # characters of notes, and of summary, sent with a show
CAPS = ["notes", "purge_all"]


def _frame(body):
    data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    return b"gran1 %d\n" % len(data) + data


def read_request(stream):
    """-> the request dict, or None when the frame is not exactly `gran1 <1..4096>\\n` + that many bytes of a JSON object."""
    raw = b""
    while len(raw) < 16:
        c = stream.read(1)
        if not c:
            return None
        if c == b"\n":
            break
        raw += c
    else:
        return None
    parts = raw.split(b" ")
    if len(parts) != 2 or parts[0] != b"gran1" or not parts[1].isdigit() or parts[1][:1] == b"0":
        return None
    size = int(parts[1])
    if not 1 <= size <= MAX_FRAME:
        return None
    body = b""
    while len(body) < size:
        chunk = stream.read(size - len(body))
        if not chunk:
            return None
        body += chunk
    try:
        req = json.loads(body)
    except ValueError:
        return None
    return req if isinstance(req, dict) else None


def fail(code, nonce=None):
    return {"ok": False, "code": code, "nonce": nonce}


def good(nonce, **fields):
    return {"ok": True, "code": "OK", "nonce": nonce, **fields}


class Context:
    def __init__(self, run=subprocess.run, grans=GRANS, db=DB, contract=None):
        self.run, self.grans, self.db, self._contract = run, grans, db, contract

    def contract(self):
        if self._contract is None:
            sys.path.insert(0, CONTRACT_DIR)
            import granola_contract
            self._contract = granola_contract
        return self._contract

    def database(self, write=False):
        mode = "rw" if write else "ro"
        return sqlite3.connect("file:%s?mode=%s" % (self.db, mode), uri=True, timeout=10)

    def grans_run(self, args, wait):
        return self.run([self.grans, *args], capture_output=True, timeout=wait, stdin=subprocess.DEVNULL)


def signed_in(ctx):
    try:
        done = ctx.grans_run(["auth", "status"], SHORT_WAIT)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and b"Signed in" in done.stdout


def op_sync(req, ctx, nonce):
    for part in ("documents", "transcripts"):
        try:
            done = ctx.grans_run(["sync", part], SYNC_EACH)
        except subprocess.TimeoutExpired:
            return fail("GRAN_TIMEOUT", nonce)
        except (OSError, subprocess.SubprocessError):
            return fail("GRAN_SYNC_FAILED", nonce)
        if done.returncode != 0:
            return fail("GRAN_SYNC_FAILED" if signed_in(ctx) else "GRAN_AUTH_EXPIRED", nonce)
    try:
        db = ctx.database()
        documents = db.execute("select count(*) from documents where deleted_at is null").fetchone()[0]
        transcripts = db.execute("select count(distinct document_id) from transcript_utterances").fetchone()[0]
        db.close()
    except sqlite3.Error:
        return fail("GRAN_SYNC_FAILED", nonce)
    return good(nonce, documents=documents, transcripts=transcripts, exit=0)


def _calendar_start(raw):
    try:
        start = (json.loads(raw) or {}).get("start")
    except (TypeError, ValueError):
        return None
    if isinstance(start, dict):
        start = start.get("dateTime")
    return start if isinstance(start, str) and "T" in start and (start.endswith("Z") or "+" in start[10:] or "-" in start[10:]) else None


def op_list(req, ctx, nonce):
    try:
        db = ctx.database()
        meetings = []
        for doc_id, created, event, title in db.execute("select id, created_at, google_calendar_event_json, title from documents where deleted_at is null order by created_at"):
            people = [(email or name or "") for email, name in db.execute("select email, full_name from document_people where document_id=?", (doc_id,))]
            meetings.append({"id": doc_id, "start": _calendar_start(event) or created, "title": title or "", "attendees": [p for p in people if p]})
        db.close()
    except sqlite3.Error:
        return fail("GRAN_SYNC_FAILED", nonce)
    return good(nonce, meetings=meetings, caps=CAPS)


def _known(ctx, doc_id):
    try:
        db = ctx.database()
        found = db.execute("select 1 from documents where id=? and deleted_at is null", (doc_id,)).fetchone()
        db.close()
    except sqlite3.Error:
        return False
    return found is not None


def _show(ctx, doc_id):
    """-> (stdout text, None) or (None, a fixed error code)."""
    try:
        done = ctx.grans_run(["show", doc_id, "--transcript", "--json", "--no-color"], SHOW_WAIT)
    except subprocess.TimeoutExpired:
        return None, "GRAN_TIMEOUT"
    except (OSError, subprocess.SubprocessError):
        return None, "GRAN_SYNC_FAILED"
    if done.returncode != 0:
        return None, "GRAN_SYNC_FAILED" if signed_in(ctx) else "GRAN_AUTH_EXPIRED"
    if len(done.stdout) > MAX_SHOW:
        return None, "GRAN_OVERSIZE"
    try:
        return done.stdout.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "GRAN_SYNC_FAILED"


def op_show(req, ctx, nonce):
    doc_id = req.get("id")
    if not isinstance(doc_id, str) or not ID.match(doc_id):
        return fail("GRAN_BAD_FRAME", nonce)
    if not _known(ctx, doc_id):
        return fail("GRAN_NOT_FOUND", nonce)
    text, code = _show(ctx, doc_id)
    if code:
        return fail(code, nonce)
    notes, summary = _notes(ctx, doc_id)
    return good(nonce, json=text, notes=notes, summary=summary)


def _notes(ctx, doc_id):
    """-> (notes, summary): Granola's own notes (markdown, else plain) and its AI summary (the summary column, else the meeting's panels), each cut at MAX_TEXT."""
    try:
        db = ctx.database()
        row = db.execute("select notes_markdown, notes_plain, summary from documents where id=?", (doc_id,)).fetchone() or (None, None, None)
        summary = row[2] or ""
        if not summary.strip():
            summary = "\n\n".join(p for (p,) in db.execute("select content_markdown from panels where document_id=? and deleted_at is null order by created_at", (doc_id,)) if p)
        db.close()
    except sqlite3.Error:
        return "", ""
    return (row[0] or row[1] or "")[:MAX_TEXT], summary[:MAX_TEXT]


def op_purge(req, ctx, nonce):
    doc_id, want, scope = req.get("id"), req.get("content_sha256"), req.get("scope")
    if not isinstance(doc_id, str) or not ID.match(doc_id) or not isinstance(want, str) or not HEX64.match(want) or scope not in (None, "all"):
        return fail("GRAN_BAD_FRAME", nonce)
    if not _known(ctx, doc_id):
        return good(nonce, purged=0, scope="all") if scope == "all" else fail("GRAN_NOT_FOUND", nonce)   # whole-meeting purge is idempotent
    text, code = _show(ctx, doc_id)
    if code:
        return fail(code, nonce)
    contract = ctx.contract()
    try:
        envelope = contract.normalize_granz(doc_id, text)
    except contract.ContractError as error:
        return fail(error.code, nonce)
    if envelope.source_content_sha256 != want:
        return fail("GRAN_HASH_MISMATCH", nonce)
    try:
        db = ctx.database(write=True)
        db.execute("pragma secure_delete=on")
        removed = _purge_all(db, doc_id) if scope == "all" else db.execute("delete from transcript_utterances where document_id=?", (doc_id,)).rowcount
        db.commit()
        db.execute("pragma wal_checkpoint(truncate)")
        db.close()
    except sqlite3.Error:
        return fail("GRAN_SYNC_FAILED", nonce)
    if scope == "all":
        return good(nonce, purged=removed, scope="all")
    return good(nonce, purged=1 if removed > 0 else 0)


def _purge_all(db, doc_id):
    """Delete everything held for one meeting in one transaction -> the number of rows removed. Tables Granola has not created on this install are skipped."""
    have = {name for (name,) in db.execute("select name from sqlite_master where type='table'")}
    removed = 0
    event = None
    row = db.execute("select google_calendar_event_json from documents where id=?", (doc_id,)).fetchone()
    try:
        event = (json.loads(row[0]) or {}).get("id") if row and row[0] else None
    except (TypeError, ValueError, AttributeError):
        pass
    if "chunks" in have and "embeddings" in have:
        removed += db.execute("delete from embeddings where chunk_id in (select id from chunks where document_id=?)", (doc_id,)).rowcount
    for table, column in (("chunks", "document_id"), ("panels", "document_id"), ("transcript_utterances", "document_id"), ("transcript_sync_log", "document_id"),
                          ("panel_sync_log", "document_id"), ("document_people", "document_id"), ("documents", "id")):
        if table in have:
            removed += db.execute("delete from %s where %s=?" % (table, column), (doc_id,)).rowcount
    if isinstance(event, str) and "events" in have:
        removed += db.execute("delete from events where id=?", (event,)).rowcount
    return removed


OPS = {"sync": op_sync, "list": op_list, "show": op_show, "purge": op_purge}


def handle(req, ctx):
    nonce = req.get("nonce")
    if not isinstance(nonce, str) or len(nonce) > 64:
        return fail("GRAN_BAD_FRAME")
    op = OPS.get(req.get("op")) if isinstance(req.get("op"), str) else None
    if op is None:
        return fail("GRAN_BAD_OP", nonce)
    try:
        return op(req, ctx, nonce)
    except Exception:                                              # noqa: BLE001 - fixed code only; never an exception text
        return fail("GRAN_SYNC_FAILED", nonce)


def main(stdin=None, stdout=None, ctx=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    req = read_request(stdin)
    reply = fail("GRAN_BAD_FRAME") if req is None else handle(req, ctx or Context())
    data = _frame(reply)
    if len(data) > MAX_REPLY:
        data = _frame(fail("GRAN_OVERSIZE", reply.get("nonce")))
    stdout.write(data)
    stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
